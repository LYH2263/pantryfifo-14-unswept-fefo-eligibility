"""API 层契约测试: 回包与层页一致、资格标记、错误映射。需 fastapi/httpx(Docker 内跑)。"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as c:
        yield c


def _past(days=10):
    return (date.today() - timedelta(days=days)).isoformat()


def _future(days=30):
    return (date.today() + timedelta(days=days)).isoformat()


def test_consume_flags_expired_unswept_and_syncs_layer(client):
    # item 3 (lower 层)种子只有一个过期在架批(id 4, 2025-01-01), 时序无关
    new_lot = client.post("/api/lots", json={"item_id": 3, "qty": 5, "expiry": _future()}).json()["id"]
    resp = client.post("/api/consume", json={"item_id": 3, "qty": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] and body["layer"] == "lower"
    d = body["deductions"]
    assert [x["lot_id"] for x in d] == [4, new_lot]          # 过期未扫批排最前
    assert [x["expired_unswept"] for x in d] == [True, False]
    # 回包与层页一致: 层余量同步, 扣尽的批不得"回包已扣、层上还在"
    shelf = client.get("/api/fridge", params={"layer": "lower"}).json()
    assert sum(x["qty_remain"] for x in shelf) == body["layer_remaining"]
    lots = {x["id"]: x for x in shelf}
    for x in d:
        if x["lot_remaining_after"] == 0:
            assert x["lot_id"] not in lots
        else:
            assert lots[x["lot_id"]]["qty_remain"] == x["lot_remaining_after"]


def test_dirty_negative_seed_lot_never_in_deductions(client):
    # item 2 种子含脏负量批(id 5, qty -3): 任何资格下都不得被扣
    resp = client.post("/api/consume", json={"item_id": 2, "qty": 1})
    assert resp.status_code == 200
    assert all(x["lot_id"] != 5 for x in resp.json()["deductions"])


def test_swept_lot_is_not_deducted(client):
    # 下架提交已标 expired 的批, 后续消费不得再扣
    lot = client.post("/api/lots", json={"item_id": 1, "qty": 3, "expiry": _past()}).json()["id"]
    swept = client.post("/api/expire-sweep").json()["expired_ids"]
    assert lot in swept
    resp = client.post("/api/consume", json={"item_id": 1, "qty": 999})
    assert resp.status_code == 409
    assert all(x["lot_id"] != lot for x in resp.json()["detail"]["deductions"])
    assert lot not in client.post("/api/expire-sweep").json()["expired_ids"]  # 幂等


def test_short_409_and_qty_400(client):
    assert client.post("/api/consume", json={"item_id": 2, "qty": 9999}).status_code == 409
    assert client.post("/api/consume", json={"item_id": 2, "qty": 0}).status_code == 400
    # 短扣与非法量都不落库: 在架批余量原样
    lots = {x["id"]: x for x in client.get("/api/fridge", params={"layer": "mid"}).json()}
    assert lots[3]["qty_remain"] == 12
