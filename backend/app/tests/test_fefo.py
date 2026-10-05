from app.engines.fefo import consume_fefo, expire_lots, sort_lots_fefo

def test_fefo_order():
    lots = [
        {"id": 2, "qty_remain": 3, "expiry": "2026-02-01"},
        {"id": 1, "qty_remain": 2, "expiry": "2026-01-10"},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [1, 2]
    r = consume_fefo(lots, 3)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["take"] == 2
    assert r["deductions"][1]["take"] == 1

def test_short():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2026-01-01"}], 5)
    assert r["ok"] is False and r["short"] == 4

def test_expire():
    ids = expire_lots([
        {"id": 1, "qty_remain": 1, "expiry": "2025-01-01"},
        {"id": 2, "qty_remain": 1, "expiry": "2027-01-01"},
    ], "2026-01-01")
    assert ids == [1]

def test_dirty_negative_never_eligible():
    # 脏负量批即使到期最早, 两种资格路径(临期/未扫过期)都不得进入 deductions
    lots = [
        {"id": 9, "qty_remain": -3, "expiry": "2020-01-01"},
        {"id": 1, "qty_remain": 2, "expiry": "2026-01-10"},
    ]
    for today in (None, "2026-10-05"):
        r = consume_fefo(lots, 2, today=today)
        assert r["ok"] and [d["lot_id"] for d in r["deductions"]] == [1]

def test_zero_and_negative_qty_skipped():
    lots = [
        {"id": 1, "qty_remain": 0, "expiry": "2020-01-01"},
        {"id": 2, "qty_remain": -1, "expiry": "2019-01-01"},
        {"id": 3, "qty_remain": 1, "expiry": "2027-01-01"},
    ]
    r = consume_fefo(lots, 1, today="2026-10-05")
    assert r["ok"] and [d["lot_id"] for d in r["deductions"]] == [3]

def test_expired_unswept_flag():
    # 到期日早于今天但仍 on_shelf 的批: 具备未扫过期资格, 排在最前并被标记
    lots = [
        {"id": 1, "qty_remain": 2, "expiry": "2026-09-01"},
        {"id": 2, "qty_remain": 5, "expiry": "2027-01-01"},
    ]
    r = consume_fefo(lots, 3, today="2026-10-05")
    assert r["ok"]
    assert [d["lot_id"] for d in r["deductions"]] == [1, 2]
    assert [d["expired_unswept"] for d in r["deductions"]] == [True, False]

def test_expired_unswept_flag_defaults_false_without_today():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2020-01-01"}], 1)
    assert r["ok"] and r["deductions"][0]["expired_unswept"] is False
