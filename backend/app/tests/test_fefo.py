from app.engines.fefo import consume_fefo, expire_lots, sort_lots_fefo

TODAY = "2026-10-05"


def lot(id, qty, expiry, dq="clean"):
    return {"id": id, "qty_remain": qty, "expiry": expiry, "data_quality": dq, "status": "on_shelf"}


def test_fefo_order():
    lots = [lot(2, 3, "2026-11-01"), lot(1, 2, "2026-10-10")]
    assert [l["id"] for l in sort_lots_fefo(lots, TODAY)] == [1, 2]
    r = consume_fefo(lots, 3, TODAY)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["take"] == 2
    assert r["deductions"][1]["take"] == 1


def test_short():
    r = consume_fefo([lot(1, 1, "2026-11-01")], 5, TODAY)
    assert r["ok"] is False and r["short"] == 4


def test_expire():
    ids = expire_lots([
        {"id": 1, "qty_remain": 1, "expiry": "2025-01-01"},
        {"id": 2, "qty_remain": 1, "expiry": "2027-01-01"},
    ], "2026-01-01")
    assert ids == [1]


def test_expiry_day_is_fresh_not_fallback():
    # expiry == today is still fresh tier
    lots = [lot(1, 2, "2026-10-05"), lot(2, 2, "2026-10-10")]
    r = consume_fefo(lots, 1, TODAY)
    assert r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["tier"] == "fresh"


def test_unscanned_expired_only_consumed_when_fresh_runs_short():
    # fresh supply covers demand: expired lot must not be touched
    lots = [lot(1, 3, "2026-10-01"), lot(2, 5, "2026-10-20")]
    r = consume_fefo(lots, 4, TODAY)
    assert r["ok"]
    assert [d["lot_id"] for d in r["deductions"]] == [2]

    # fresh supply short: expired lot is reachable as fallback
    r = consume_fefo(lots, 7, TODAY)
    # 5 fresh + 3 expired = 8, so it must succeed
    assert r["ok"], r
    assert [d["lot_id"] for d in r["deductions"]] == [2, 1]
    assert r["deductions"][1]["tier"] == "expired"


def test_unscanned_expired_fallback_when_no_fresh():
    # layer still has margin (expired lot only) — it must be consumable, not pinned forever
    r = consume_fefo([lot(1, 3, "2026-09-20")], 2, TODAY)
    assert r["ok"]
    assert r["deductions"][0]["lot_id"] == 1
    assert r["deductions"][0]["tier"] == "expired"
    assert r["deductions"][0]["take"] == 2


def test_fresh_ordering_ignores_expired_tier_even_if_earlier_expiry():
    # expired lot has the earliest expiry but must sort AFTER all fresh lots
    lots = [lot(1, 4, "2026-09-01"), lot(2, 2, "2026-11-01"), lot(3, 2, "2026-10-20")]
    assert [l["id"] for l in sort_lots_fefo(lots, TODAY)] == [3, 2, 1]


def test_dirty_lot_excluded_even_when_expired_fallback_needed():
    # only a dirty negative and a dirty expired lot exist: no eligible supply
    r = consume_fefo([lot(1, -3, "2026-12-01", dq="dirty")], 1, TODAY)
    assert r["ok"] is False and r["short"] == 1 and r["deductions"] == []

    # dirty positive expired lot cannot cover demand either
    r = consume_fefo([lot(2, 5, "2026-09-01", dq="dirty")], 1, TODAY)
    assert r["ok"] is False and r["short"] == 1 and r["deductions"] == []


def test_dirty_never_mixed_into_deductions():
    # fresh + dirty(neg) + expired-clean: dirty must never appear; expired is fallback
    lots = [lot(1, 2, "2026-10-20"), lot(2, -3, "2026-12-01", dq="dirty"),
            lot(3, 2, "2026-10-01")]
    r = consume_fefo(lots, 4, TODAY)
    assert r["ok"]
    ids = [d["lot_id"] for d in r["deductions"]]
    assert 2 not in ids
    assert ids == [1, 3]


def test_legacy_behaviour_without_today():
    # today=None: plain FEFO over positive lots regardless of expiry
    lots = [lot(1, 2, "2026-09-01"), lot(2, 3, "2026-11-01")]
    r = consume_fefo(lots, 2)
    assert [d["lot_id"] for d in r["deductions"]] == [1]
