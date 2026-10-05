"""服务层资格与并发测试: 只依赖 stdlib, 不 import fastapi。

pytest 可直接收集; 无 pytest 的环境可 `python3 -m app.tests.test_pantry_service` 跑。
"""
import os, sys, tempfile, threading
from contextlib import contextmanager

from app import seed
from app.db import connect
from app.services.pantry import consume_item, sweep_expired

TODAY = "2026-10-05"
ROUNDS = 10  # 并发场景重复轮次, 提高争行命中率


@contextmanager
def fresh_db():
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = d
        try:
            seed.init_db()
            yield
        finally:
            os.environ.pop("DATA_DIR", None)
            if old is not None:
                os.environ["DATA_DIR"] = old


def _reset_lots():
    c = connect(); c.execute("DELETE FROM lots"); c.close()


def _insert_lot(item_id, qty, expiry, status="on_shelf", quality="clean"):
    c = connect()
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (item_id, qty, qty, expiry, status, quality))
    lid = cur.lastrowid
    c.close()
    return lid


def _lot(lot_id):
    c = connect()
    r = dict(c.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone())
    c.close()
    return r


def test_unscanned_expired_is_deducted_first():
    # 未扫过期批在全层仍有余量时必须扣得到, 且按 FEFO 排最前
    with fresh_db():
        _reset_lots()
        old = _insert_lot(1, 2, "2026-09-01")   # 过期仍在架(未扫)
        new = _insert_lot(1, 5, "2027-01-01")
        r = consume_item(connect(), 1, 3, TODAY)
        assert r["ok"], r
        assert [d["lot_id"] for d in r["deductions"]] == [old, new]
        assert r["deductions"][0]["expired_unswept"] is True
        assert r["deductions"][1]["expired_unswept"] is False
        assert _lot(old)["status"] == "consumed" and _lot(old)["qty_remain"] == 0
        assert _lot(new)["qty_remain"] == 4
        assert r["item_remaining"] == 4 and r["layer"] == "upper"
        assert r["layer_remaining"] == 4


def test_already_expired_lot_is_not_deducted():
    # 下架提交已标 expired 的批, 消费一粒都不得扣
    with fresh_db():
        _reset_lots()
        lid = _insert_lot(1, 3, "2026-09-01", status="expired")
        r = consume_item(connect(), 1, 2, TODAY)
        assert not r["ok"] and r["reason"] == "short"
        assert all(d["lot_id"] != lid for d in r["deductions"])
        assert _lot(lid)["qty_remain"] == 3


def test_dirty_negative_lot_never_deducted():
    # 脏负量批: 到期最早也不得入 deductions, 余量原样保留
    with fresh_db():
        _reset_lots()
        neg = _insert_lot(1, -3, "2020-01-01", quality="dirty")
        good = _insert_lot(1, 2, "2027-01-01")
        r = consume_item(connect(), 1, 2, TODAY)
        assert r["ok"] and [d["lot_id"] for d in r["deductions"]] == [good]
        assert _lot(neg)["qty_remain"] == -3


def test_concurrent_consume_never_overshoots():
    # 两笔消费争同一行: 只允许一笔成功, 余量绝不扣成负
    for _ in range(ROUNDS):
        with fresh_db():
            _reset_lots()
            lid = _insert_lot(1, 5, "2027-01-01")
            barrier, results = threading.Barrier(2), []

            def worker():
                barrier.wait()
                results.append(consume_item(connect(), 1, 4, TODAY))

            ts = [threading.Thread(target=worker) for _ in range(2)]
            [t.start() for t in ts]; [t.join() for t in ts]
            oks = [r for r in results if r["ok"]]
            shorts = [r for r in results if not r["ok"] and r["reason"] == "short"]
            assert len(oks) == 1 and len(shorts) == 1, results
            assert _lot(lid)["qty_remain"] == 1


def test_consume_vs_sweep_race_stays_consistent():
    # 消费与过期下架争同一行(过期仍在架): 两种时序都合法, 但不许出现
    # "扣到已 expired 的批"或"扣了还按原量在架"的中间态
    for _ in range(ROUNDS):
        with fresh_db():
            _reset_lots()
            lid = _insert_lot(1, 3, "2026-09-01")
            barrier, out = threading.Barrier(2), {}

            def consumer():
                barrier.wait()
                out["r"] = consume_item(connect(), 1, 2, TODAY)

            def sweeper():
                barrier.wait()
                out["ids"] = sweep_expired(connect(), TODAY)

            ts = [threading.Thread(target=consumer), threading.Thread(target=sweeper)]
            [t.start() for t in ts]; [t.join() for t in ts]
            lot = _lot(lid)
            assert lot["qty_remain"] >= 0
            assert out["ids"] == [lid]  # 余量始终 >0, 下架总会把它标 expired
            assert lot["status"] == "expired"
            if out["r"]["ok"]:
                # 消费先拿到锁: 扣时批在架 → 扣 2 余 1, 随后被下架标记
                assert lot["qty_remain"] == 1
                assert out["r"]["deductions"][0]["expired_unswept"] is True
            else:
                # 下架先拿到锁: 消费看到的是已 expired 的批 → 一粒不扣
                assert out["r"]["reason"] == "short"
                assert lot["qty_remain"] == 3


def test_response_matches_shelf_state():
    # 回包 deductions 与层上状态一致: 不得"回包已扣、层上还在(原量)"
    with fresh_db():
        _reset_lots()
        a = _insert_lot(1, 2, "2026-09-01")
        b = _insert_lot(1, 5, "2027-01-01")
        _insert_lot(2, 7, "2027-01-01")  # mid 层, 不计入 upper 余量
        r = consume_item(connect(), 1, 4, TODAY)
        assert r["ok"]
        for d in r["deductions"]:
            lot = _lot(d["lot_id"])
            assert lot["qty_remain"] == d["lot_remaining_after"]
            if d["lot_remaining_after"] == 0:
                assert lot["status"] == "consumed"
        assert r["item_remaining"] == 3
        assert r["layer_remaining"] == 3
        assert _lot(a)["qty_remain"] == 0 and _lot(b)["qty_remain"] == 3


def test_short_writes_nothing():
    # 余量不足: 一粒不落库, 也不记消费流水
    with fresh_db():
        _reset_lots()
        lid = _insert_lot(1, 1, "2027-01-01")
        r = consume_item(connect(), 1, 5, TODAY)
        assert not r["ok"] and r["reason"] == "short"
        assert _lot(lid)["qty_remain"] == 1
        c = connect()
        n = c.execute("SELECT COUNT(*) c FROM consumptions").fetchone()["c"]
        c.close()
        assert n == 0


def test_non_positive_qty_rejected():
    with fresh_db():
        _reset_lots()
        _insert_lot(1, 1, "2027-01-01")
        r = consume_item(connect(), 1, 0, TODAY)
        assert not r["ok"] and r["reason"] == "qty_non_positive"


def test_sweep_returns_only_actually_expired():
    with fresh_db():
        _reset_lots()
        a = _insert_lot(1, 1, "2026-09-01")                    # 过期在架 → 下架
        b = _insert_lot(1, 1, "2027-01-01")                    # 未到期 → 不动
        _insert_lot(1, 1, "2026-08-01", status="consumed")     # 已消费 → 不动
        _insert_lot(1, 0, "2026-08-01")                        # 零量在架 → 不动
        assert sweep_expired(connect(), TODAY) == [a]
        assert sweep_expired(connect(), TODAY) == []           # 幂等, 不重复回包
        assert _lot(b)["status"] == "on_shelf"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed = 1
            print(f"FAIL {fn.__name__}: {e!r}")
    sys.exit(failed)
