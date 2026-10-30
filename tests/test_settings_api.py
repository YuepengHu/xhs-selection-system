import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from xhs_monitor import app as app_module
from xhs_monitor import settings as cfg
from xhs_monitor.fetcher import Fetched
from xhs_monitor.store import Store

ID = "67ed05f404afe300019465be"
NOW = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)


def snap(store, ts, sales, step=1, item=ID):
    store.add_snapshot(item, ts, raw=f"已售{sales}", low=sales, step=step, price=9.9, error=None)


def test_long_delta_uses_snapshot_at_or_before_window_start():
    s = Store()
    s.add_item(ID, "未分类", NOW)
    for days, sales in [(40, 100), (31, 120), (8, 200), (6, 230), (0, 260)]:
        snap(s, NOW - timedelta(days=days), sales)
    latest = s.last_snapshot(ID)
    assert app_module._long_delta(s, ID, latest, NOW, 7).lo == 60
    assert app_module._long_delta(s, ID, latest, NOW, 30).lo == 140


def test_long_delta_none_without_enough_history():
    s = Store()
    s.add_item(ID, "未分类", NOW)
    snap(s, NOW - timedelta(days=2), 100)
    snap(s, NOW, 110)
    assert app_module._long_delta(s, ID, s.last_snapshot(ID), NOW, 7) is None


def test_settings_defaults_update_and_mask(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "env-token")
    s = Store()
    d = cfg.load(s)
    assert d.pushplus_token == "env-token" and [r.min_hour_delta for r in d.rules] == [5, 2]
    cfg.update(s, {"rules": [{"name": "新规则", "min_hour_delta": 3, "min_price": ""}], "active_start": 9})
    d = cfg.load(s)
    assert d.rules[0].name == "新规则" and d.rules[0].min_price is None and d.active_start == 9
    assert cfg.public(d)["pushplus_token"] == cfg.MASK
    cfg.update(s, {"pushplus_token": cfg.MASK})
    assert cfg.load(s).pushplus_token == "env-token"
    cfg.update(s, {"pushplus_token": ""})
    assert cfg.load(s).pushplus_token == ""


@pytest.mark.parametrize("patch", [
    {"rules": [{"name": "", "min_hour_delta": 1}]},
    {"rules": [{"name": "x", "min_hour_delta": 0}]},
    {"rules": [{"name": "x", "min_hour_delta": 1, "min_price": -1}]},
    {"active_start": 20, "active_end": 10},
    {"active_end": 24},
    {"dingtalk_webhook": "http://example.com/x"},
])
def test_settings_validation(patch):
    with pytest.raises(cfg.SettingsError):
        cfg.update(Store(), patch)


class FakeFetcherCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def fetch(self, item_id):
        return Fetched("已售100", "已售 3.8万", "测试商品", 9.9)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "Fetcher", FakeFetcherCtx)
    monkeypatch.setenv("XHS_NO_SCHEDULER", "1")
    for k in ("PUSHPLUS_TOKEN", "DINGTALK_WEBHOOK", "DINGTALK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(app_module.create_app(":memory:")) as c:
        yield c


def test_settings_api_roundtrip_and_validation(client):
    assert client.get("/api/settings").json()["active_start"] == 10
    r = client.put("/api/settings", json={"pushplus_token": "secret-token", "active_start": 8})
    assert r.status_code == 200 and r.json()["pushplus_token"] == cfg.MASK and r.json()["active_start"] == 8
    assert "secret-token" not in client.get("/api/settings").text
    assert client.put("/api/settings", json={"active_end": 30}).status_code == 400


def test_notify_test_reports_unconfigured(client):
    assert client.post("/api/notify/test").json() == {"configured": False, "result": {}}


def test_add_item_then_fetch_and_list(client):
    r = client.post("/api/items", json={"text": ID, "category": "教资"})
    assert r.json()["added"] == [ID]
    for _ in range(40):
        items = client.get("/api/items").json()["items"]
        if items and items[0]["sales"]:
            break
        time.sleep(0.1)
    assert items[0]["sales"] == "已售100" and items[0]["title"] == "测试商品"
    assert {"d7", "d30"} <= items[0].keys()
    assert client.get("/api/categories").json() == ["教资"]
    assert client.post("/api/items", json={"text": ID}).json()["existing"] == [ID]
