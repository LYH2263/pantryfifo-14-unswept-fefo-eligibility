"""Serialized consume / sweep transactions.

Every mutating transaction starts with BEGIN IMMEDIATE so a consume and an
expiry sweep (or two consumes) can never plan against the same row snapshot
and commit independently. Inside the lock the lot rows are re-read, the FEFO
plan is rebuilt, and each deduction is applied with a guarded UPDATE whose
WHERE clause re-checks status / data_quality / remaining quantity. A guard
that affects 0 rows means the plan went stale and the whole transaction is
retried from scratch — a deduction can therefore never land on a lot a
concurrent sweep just marked expired, and can never drive a lot negative.
"""

import json
import sqlite3
import time
from datetime import date, datetime, timezone

from app.engines.fefo import consume_fefo, expire_lots

MAX_TRIES = 3
BUSY_WAIT_S = 0.05


def today_iso() -> str:
    return date.today().isoformat()


class ConsumeError(Exception):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(payload.get("reason", "consume_error"))


def _immediate(conn: sqlite3.Connection):
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")


def _rollback(conn: sqlite3.Connection):
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        pass  # no active transaction (e.g. BEGIN IMMEDIATE itself was busy)


def _eligible_tier(lot: dict, today: str) -> str:
    """Post-state eligibility label for API responses."""
    if float(lot.get("qty_remain", 0)) <= 0:
        return ""
    if lot.get("data_quality", "clean") != "clean":
        return ""
    exp = lot.get("expiry")
    if not exp:
        return "fresh"
    return "fresh" if exp >= today else "expired"


def _item_lots(conn: sqlite3.Connection, item_id: int, today: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        """SELECT lots.id, lots.item_id, lots.qty_in, lots.qty_remain, lots.expiry,
                  lots.status, lots.data_quality, items.name, items.layer, items.unit
           FROM lots JOIN items ON items.id=lots.item_id
           WHERE lots.item_id=? ORDER BY lots.expiry, lots.id""", (item_id,))]
    for r in rows:
        r["eligible_tier"] = _eligible_tier(r, today) if r["status"] == "on_shelf" else ""
    return rows


def _layer_summary(conn: sqlite3.Connection, today: str) -> list[dict]:
    """Whole-fridge per-layer margin, computed inside the same transaction.

    usable = on_shelf, positive, clean (unscanned-expired clean lots are
    included: they ARE reachable via the expired fallback tier).
    """
    rows = conn.execute(
        """SELECT items.layer AS layer,
                  ROUND(SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0
                                 AND lots.data_quality='clean'
                                 THEN lots.qty_remain ELSE 0 END),3) AS usable,
                  SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0 THEN 1 ELSE 0 END) AS lots_count,
                  SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0
                            AND lots.expiry IS NOT NULL AND lots.expiry<? THEN 1 ELSE 0 END) AS expired_count,
                  SUM(CASE WHEN lots.status='on_shelf' AND lots.qty_remain>0
                            AND lots.data_quality!='clean' THEN 1 ELSE 0 END) AS dirty_count
           FROM lots JOIN items ON items.id=lots.item_id
           GROUP BY items.layer ORDER BY items.layer""", (today,)).fetchall()
    return [dict(r) for r in rows]


def _expired_unscanned(conn: sqlite3.Connection, item_id: int, today: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        """SELECT id, qty_remain, expiry, status, data_quality FROM lots
           WHERE item_id=? AND status='on_shelf' AND qty_remain>0
             AND expiry IS NOT NULL AND expiry<? ORDER BY expiry, id""", (item_id, today))]
    for r in rows:
        r["eligible_tier"] = _eligible_tier(r, today)
    return rows


def run_consume(conn: sqlite3.Connection, item_id: int, qty: float, note: str,
                today: str | None = None) -> dict:
    today = today or today_iso()
    last_locked = None
    for attempt in range(MAX_TRIES):
        try:
            return _consume_once(conn, item_id, qty, note, today)
        except sqlite3.OperationalError as e:
            # database is locked / busy — another writer holds IMMEDIATE.
            last_locked = str(e)
            time.sleep(BUSY_WAIT_S * (attempt + 1))
        except _StalePlan:
            # row changed between plan and guarded apply — replan.
            time.sleep(BUSY_WAIT_S * (attempt + 1))
    raise sqlite3.OperationalError(last_locked or "consume retries exhausted")


class _StalePlan(Exception):
    pass


def _consume_once(conn: sqlite3.Connection, item_id: int, qty: float,
                  note: str, today: str) -> dict:
    _immediate(conn)
    try:
        item = conn.execute("SELECT id FROM items WHERE id=?", (item_id,)).fetchone()
        if not item:
            _rollback(conn)
            raise ConsumeError(404, {"reason": "item_not_found", "deductions": []})

        lots = [dict(r) for r in conn.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0",
            (item_id,))]
        plan = consume_fefo(lots, qty, today)

        if not plan["ok"] and plan["reason"] == "qty_non_positive":
            _rollback(conn)
            raise ConsumeError(400, {"reason": "qty_non_positive", "deductions": []})

        if not plan["ok"]:
            # Short: nothing is written. Return the unapplied plan plus the
            # current state so the client can see which expired lots exist.
            payload = {
                "ok": False, "reason": "short", "short": plan["short"],
                "qty": float(qty), "today": today,
                "deductions": plan["deductions"],  # planned, NOT applied
                "applied": [],
                "expired_unscanned": _expired_unscanned(conn, item_id, today),
                "item_lots": _item_lots(conn, item_id, today),
                "layers": _layer_summary(conn, today),
            }
            _rollback(conn)
            raise ConsumeError(409, payload)

        applied = []
        for d in plan["deductions"]:
            cur = conn.execute(
                """UPDATE lots
                   SET qty_remain=ROUND(qty_remain - ?, 3)
                   WHERE id=? AND status='on_shelf' AND data_quality='clean'
                     AND qty_remain >= ?""",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                # Sweep flipped it to expired, or the row is otherwise no
                # longer eligible. Abort and rebuild the plan.
                raise _StalePlan(d["lot_id"])
            rem = conn.execute("SELECT qty_remain FROM lots WHERE id=?",
                               (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 1e-9:
                conn.execute(
                    "UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?",
                    (d["lot_id"],))
                rem = 0.0
            applied.append({
                "lot_id": d["lot_id"],
                "take": d["take"],
                "expiry": d["expiry"],
                "tier": d["tier"],
                "was_expired_on_shelf": d["tier"] == "expired",
                "remain": round(float(rem), 3),
                "status": "consumed" if rem == 0.0 else "on_shelf",
            })

        result_log = {"ok": True, "deductions": applied, "short": 0.0, "today": today}
        conn.execute(
            "INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
            (note, json.dumps(result_log), datetime.now(timezone.utc).isoformat()))

        payload = {
            "ok": True, "reason": "", "item_id": item_id,
            "qty": float(qty), "today": today, "short": 0.0,
            "deductions": applied,
            "applied": applied,
            "expired_unscanned": _expired_unscanned(conn, item_id, today),
            "item_lots": _item_lots(conn, item_id, today),
            "layers": _layer_summary(conn, today),
        }
        conn.execute("COMMIT")
        return payload
    except ConsumeError:
        raise
    except _StalePlan:
        _rollback(conn)
        raise
    except Exception:
        _rollback(conn)
        raise


def run_sweep(conn: sqlite3.Connection, today: str | None = None) -> dict:
    today = today or today_iso()
    for attempt in range(MAX_TRIES):
        try:
            return _sweep_once(conn, today)
        except sqlite3.OperationalError:
            time.sleep(BUSY_WAIT_S * (attempt + 1))
    raise


def _sweep_once(conn: sqlite3.Connection, today: str) -> dict:
    _immediate(conn)
    try:
        lots = [dict(r) for r in conn.execute(
            "SELECT * FROM lots WHERE status='on_shelf'")]
        effective = []
        for lid in expire_lots(lots, today):
            cur = conn.execute(
                """UPDATE lots SET status='expired'
                   WHERE id=? AND status='on_shelf' AND qty_remain>0""",
                (lid,))
            if cur.rowcount == 1:
                effective.append(lid)
        payload = {"expired_ids": effective, "today": today,
                   "layers": _layer_summary(conn, today)}
        conn.execute("COMMIT")
        return payload
    except Exception:
        _rollback(conn)
        raise
