"""消费/过期下架的写路径: 在写锁事务内做资格判定与落库, 保证回包与库一致。

并发约定(消费 vs 消费、消费 vs 过期下架会争同一行):
  - 整个"读快照 → FEFO 计划 → 守卫 UPDATE → 回包汇总"都在 write_tx
    (BEGIN IMMEDIATE) 内完成, 其它写者要么等锁要么看到提交后的状态。
  - 每条扣减用 `status='on_shelf' AND qty_remain >= take` 守卫:
    不会扣到下架提交已标 expired 的批, 也不会把行扣成脏负量。
"""
import json
from datetime import datetime, timezone

from app.db import write_tx
from app.engines.fefo import consume_fefo, expire_lots

_EPS = 1e-9


class LotStateChanged(Exception):
    """守卫 UPDATE 未命中: 行在锁外被改成不可扣状态(防御性, 正常不会发生)。"""

    def __init__(self, lot_id):
        super().__init__(f"lot {lot_id} changed underfoot")
        self.lot_id = lot_id


def _on_shelf_lots(c, item_id: int) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0",
        (item_id,))]


def _remaining(c, where: str, arg) -> float:
    return c.execute(
        f"SELECT COALESCE(SUM(qty_remain),0) s FROM lots "
        f"JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf' AND {where}",
        (arg,)).fetchone()["s"]


def consume_item(c, item_id: int, qty: float, today: str, note: str = "") -> dict:
    """FEFO 扣减一笔消费。未 ok 时不落任何写; ok 时回包即提交后的库状态。"""
    with write_tx(c):
        # 锁内取资格快照: 此刻已标 expired 的批不在候选里, 脏负量批被 SQL 与
        # 引擎双层过滤, 无论临期资格还是未扫过期资格都进不了 deductions。
        result = consume_fefo(_on_shelf_lots(c, item_id), qty, today=today)
        if not result["ok"]:
            return result
        for d in result["deductions"]:
            cur = c.execute(
                "UPDATE lots SET qty_remain = qty_remain - ? "
                "WHERE id=? AND status='on_shelf' AND qty_remain >= ?",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                raise LotStateChanged(d["lot_id"])
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?",
                            (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= _EPS:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?",
                          (d["lot_id"],))
                rem = 0.0
            d["lot_remaining_after"] = rem
        c.execute(
            "INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
            (note, json.dumps(result), datetime.now(timezone.utc).isoformat()))
        row = c.execute("SELECT layer FROM items WHERE id=?", (item_id,)).fetchone()
        result["item_remaining"] = _remaining(c, "lots.item_id=?", item_id)
        result["layer"] = row["layer"] if row else None
        result["layer_remaining"] = (
            _remaining(c, "items.layer=?", result["layer"]) if row else 0.0)
    return result


def sweep_expired(c, today: str) -> list[int]:
    """过期下架。只回包实际从 on_shelf 迁到 expired 的批 id。"""
    with write_tx(c):
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        done = []
        for i in expire_lots(lots, today):
            cur = c.execute(
                "UPDATE lots SET status='expired' WHERE id=? AND status='on_shelf'", (i,))
            if cur.rowcount == 1:
                done.append(i)
    return done
