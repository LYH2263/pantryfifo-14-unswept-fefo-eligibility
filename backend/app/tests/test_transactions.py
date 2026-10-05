"""Transaction-level tests over a real sqlite file (stdlib unittest).

Covers the eligibility/consistency contract:
- deductions never land on a lot a committed sweep marked expired
- unscanned-expired lots are reachable as fallback, fresh lots first
- dirty/negative lots never appear in deductions
- response (item_lots / layers) matches committed DB state
- consume vs expire-sweep serialize on BEGIN IMMEDIATE
"""

import os
import sqlite3
import tempfile
import threading
import unittest

_TMP = tempfile.mkdtemp(prefix="pantryfifo-test-")
os.environ["DATA_DIR"] = _TMP

from app import seed  # noqa: E402
from app.db import connect  # noqa: E402
from app.transactions import ConsumeError, run_consume, run_sweep  # noqa: E402

TODAY = "2026-10-05"


def fresh_db():
    path = os.path.join(_TMP, "pantryfifo.db")
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            os.remove(p)
    seed.init_db()
    # seed lots are fixed demo data; tests need a clean lot ledger
    c = connect()
    c.execute("DELETE FROM lots")
    c.execute("DELETE FROM consumptions")
    c.commit(); c.close()


def add_lot(item_id, qty, expiry, status="on_shelf", dq="clean"):
    c = connect()
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (item_id, qty, qty, expiry, status, dq))
    c.commit(); lid = cur.lastrowid; c.close()
    return lid


def lot_row(lid):
    c = connect()
    r = dict(c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone()); c.close()
    return r


def layer_usable(layer):
    c = connect()
    r = c.execute(
        """SELECT ROUND(SUM(CASE WHEN status='on_shelf' AND qty_remain>0 AND data_quality='clean'
                                 THEN qty_remain ELSE 0 END),3) u
           FROM lots JOIN items ON items.id=lots.item_id WHERE items.layer=?""",
        (layer,)).fetchone()["u"]
    c.close()
    return r or 0.0


class ConsumeEligibilityTests(unittest.TestCase):

    def setUp(self):
        fresh_db()

    def test_does_not_deduct_committed_expired_lot(self):
        expired = add_lot(1, 2, "2026-10-01", status="expired")
        fresh = add_lot(1, 5, "2026-10-20")
        res = run_consume(connect(), 1, 3, "", TODAY)
        ids = [d["lot_id"] for d in res["deductions"]]
        self.assertEqual(ids, [fresh])
        self.assertEqual(lot_row(expired)["qty_remain"], 2)
        self.assertEqual(lot_row(expired)["status"], "expired")

    def test_unscanned_expired_is_fallback_not_first(self):
        exp = add_lot(1, 3, "2026-10-01")
        fresh = add_lot(1, 5, "2026-10-20")
        # fresh covers it: expired unscanned untouched
        res = run_consume(connect(), 1, 4, "", TODAY)
        self.assertEqual([d["lot_id"] for d in res["deductions"]], [fresh])
        self.assertEqual(lot_row(exp)["qty_remain"], 3)
        # fresh runs short: expired unscanned IS reachable
        res = run_consume(connect(), 1, 4, "", TODAY)  # only 1 fresh left -> 3 from expired
        self.assertEqual([(d["lot_id"], d["tier"]) for d in res["deductions"]],
                         [(fresh, "fresh"), (exp, "expired")])
        self.assertTrue(all(not d["was_expired_on_shelf"] for d in res["deductions"][:1]))
        self.assertTrue(res["deductions"][1]["was_expired_on_shelf"])
        self.assertEqual(lot_row(exp)["status"], "consumed")

    def test_expired_only_layer_still_consumable(self):
        # no fresh margin, one unscanned-expired lot: must not be pinned forever
        exp = add_lot(1, 3, "2026-09-20")
        res = run_consume(connect(), 1, 2, "", TODAY)
        self.assertEqual([d["lot_id"] for d in res["deductions"]], [exp])
        self.assertEqual(lot_row(exp)["qty_remain"], 1)

    def test_dirty_negative_never_in_deductions(self):
        dirty = add_lot(2, -3, "2026-12-01", dq="dirty")
        with self.assertRaises(ConsumeError) as cm:
            run_consume(connect(), 2, 1, "", TODAY)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.payload["deductions"], [])
        self.assertEqual(cm.exception.payload["short"], 1.0)
        self.assertEqual(lot_row(dirty)["qty_remain"], -3)

    def test_dirty_expired_never_laundered(self):
        dirty = add_lot(1, 5, "2026-09-01", dq="dirty")
        with self.assertRaises(ConsumeError) as cm:
            run_consume(connect(), 1, 1, "", TODAY)
        self.assertEqual(cm.exception.payload["deductions"], [])
        self.assertEqual(lot_row(dirty)["status"], "on_shelf")
        self.assertEqual(lot_row(dirty)["qty_remain"], 5)

    def test_no_negative_after_partial_take(self):
        lid = add_lot(1, 2, "2026-10-20")
        run_consume(connect(), 1, 2, "", TODAY)
        r = lot_row(lid)
        self.assertEqual(r["qty_remain"], 0)
        self.assertEqual(r["status"], "consumed")

    def test_response_matches_committed_state(self):
        add_lot(1, 3, "2026-10-01")  # expired unscanned on upper layer
        add_lot(1, 5, "2026-10-20")
        res = run_consume(connect(), 1, 4, "", TODAY)
        # item_lots in response equals a fresh read
        c = connect()
        db_rows = {r["id"]: dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=1")}
        c.close()
        for r in res["item_lots"]:
            self.assertAlmostEqual(r["qty_remain"], db_rows[r["id"]]["qty_remain"], places=3)
            self.assertEqual(r["status"], db_rows[r["id"]]["status"])
        # layer totals in response equal committed totals
        for L in res["layers"]:
            self.assertAlmostEqual(L["usable"], layer_usable(L["layer"]), places=3)
        # upper layer usable = (2 seed milks: both expired/dirty excluded...) just check nonneg
        self.assertTrue(all(L["usable"] >= 0 for L in res["layers"]))
        # expired_unscanned reflects post-commit remaining expired lots
        self.assertTrue(all(u["status"] == "on_shelf" for u in res["expired_unscanned"]))

    def test_sweep_then_consume_concurrent(self):
        """Sweep holds the write lock and marks the only eligible lot expired.

        Consume must block on BEGIN IMMEDIATE and, once it gets the lock,
        replan against the new state — it must not deduct the expired lot.
        """
        lid = add_lot(1, 3, "2026-10-01")
        locked = threading.Event()
        release = threading.Event()
        done = threading.Event()
        outcome = {}

        def sweeper():
            c = connect()
            c.execute("PRAGMA busy_timeout=3000")
            c.isolation_level = None
            c.execute("BEGIN IMMEDIATE")
            locked.set()
            release.wait(timeout=5)
            c.execute("UPDATE lots SET status='expired' WHERE id=?", (lid,))
            c.execute("COMMIT")
            c.close()

        def consumer():
            locked.wait(timeout=5)
            try:
                outcome["res"] = run_consume(connect(), 1, 1, "", TODAY)
            except ConsumeError as e:
                outcome["err"] = e
            done.set()

        t1 = threading.Thread(target=sweeper)
        t2 = threading.Thread(target=consumer)
        t1.start()
        locked.wait(timeout=5)
        t2.start()
        # give the consumer time to block on the write lock, then release sweep
        threading.Event().wait(0.3)
        release.set()
        t1.join(timeout=5)
        done.wait(timeout=5)
        t2.join(timeout=5)

        self.assertIn("err", outcome)  # no eligible supply once sweep committed
        self.assertEqual(outcome["err"].payload["deductions"], [])
        self.assertEqual(lot_row(lid)["status"], "expired")
        self.assertEqual(lot_row(lid)["qty_remain"], 3)

    def test_consume_then_sweep_no_double_claim(self):
        """Consume drains the expired-unscanned lot completely; sweep must not refind it."""
        lid = add_lot(1, 2, "2026-10-01")
        run_consume(connect(), 1, 2, "", TODAY)
        sweep = run_sweep(connect(), TODAY)
        self.assertNotIn(lid, sweep["expired_ids"])
        self.assertEqual(lot_row(lid)["status"], "consumed")

    def test_two_consumes_serialize(self):
        lid = add_lot(1, 3, "2026-10-20")
        outcomes = []

        def consume(qty):
            try:
                outcomes.append(("ok", run_consume(connect(), 1, qty, "", TODAY)))
            except ConsumeError as e:
                outcomes.append(("err", e))

        t1 = threading.Thread(target=consume, args=(2,))
        t2 = threading.Thread(target=consume, args=(2,))
        t1.start(); t2.start(); t1.join(5); t2.join(5)
        # exactly one consume succeeds fully; the second is short (only 1 left)
        self.assertEqual([k for k, _ in outcomes].count("ok"), 1)
        err = next(v for k, v in outcomes if k == "err")
        self.assertEqual(err.status_code, 409)
        self.assertEqual(err.payload["short"], 1.0)
        # short consume applies nothing; first consume's 2 stands
        self.assertEqual(lot_row(lid)["qty_remain"], 1)
        self.assertEqual(lot_row(lid)["status"], "on_shelf")


if __name__ == "__main__":
    unittest.main()
