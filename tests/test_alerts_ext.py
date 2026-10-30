import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from xhs_monitor import app as app_module
from xhs_monitor import settings as cfg
from xhs_monitor.alerts import Rule, triggered
from xhs_monitor.fetcher import Fetched
from xhs_monitor.increments import TZ, Metrics, Snap, daily_series
from xhs_monitor.runner import run_once
from xhs_monitor.store import Store

ID = "67ed05f404afe300019465be"
NOW = datetime(2026, 10, 30, 3, 1, tzinfo=timezone.utc)  # 本地 11:01


def eod(days_ago, low, step=1):
    """某个本地自然日 23:30 的快照。"""
    d = (NOW.astimezone(TZ) - timedelta(days=days_ago)).replace(hour=23, minute=30)
    return Snap(d.astimezone(timezone.utc), low, step)


def test_daily_series_gives_completed_days_most_recent_first():
    snaps = [eod(4, 100), eod(3, 105), eod(2, 111), eod(1, 118)]
    out = daily_series(snaps, NOW, 3)
    assert [d.lo for d in out] == [7, 6, 5]


def test_daily_series_none_for_missing_day():
    out = daily_series([eod(3, 100), eod(1, 118)], NOW, 3)
    assert out == [None, None, None]
    out = daily_series([eod(4, 100), eod(3, 105), eod(1, 118)], NOW, 3)
    assert out[0] is None and out[1] is None and out[2].lo == 5


STREAK = Rule("连续3天>=5", kind="streak", days=3, min_daily=5)


def _hits(rules, snaps, price=9.9):
    return triggered(rules, Metrics(None, None, None), price, daily_series(snaps, NOW, 3))


def test_streak_rule_requires_every_day():
    ok = [eod(4, 100), eod(3, 105), eod(2, 111), eod(1, 118)]
    assert _hits([STREAK], ok) == [STREAK]
    assert _hits([STREAK], [eod(4, 100), eod(3, 105), eod(2, 106), eod(1, 118)]) == []
    assert _hits([STREAK], [eod(3, 105), eod(2, 111), eod(1, 118)]) == []


def test_streak_rule_price_filter_and_cooldown():
    ok = [eod(4, 100), eod(3, 105), eod(2, 111), eod(1, 118)]
    assert _hits([Rule("贵", kind="streak", days=3, min_daily=5, min_price=29)], ok, price=9.9) == []
    assert STREAK.cooldown_hours == 20 and Rule("x").cooldown_hours == 3


@pytest.mark.parametrize("rule", [
    {"name": "x", "kind": "weird"},
    {"name": "x", "kind": "streak", "days": 1},
    {"name": "x", "kind": "streak", "days": 3, "min_daily": 0},
])
def test_streak_settings_validation(rule):
    with pytest.raises(cfg.SettingsError):
        cfg.update(Store(), {"rules": [rule]})


def test_streak_settings_roundtrip():
    s = Store()
    cfg.update(s, {"rules": [{"name": "连续", "kind": "streak", "days": 4, "min_daily": 2}]})
    r = cfg.load(s).rules[0]
    assert (r.kind, r.days, r.min_daily) == ("streak", 4, 2)


class Fx:
    def __init__(self, items):
        self.items = list(items)

    async def fetch(self, item_id):
        return self.items.pop(0)


class Nt:
    def __init__(self):
        self.sent = []

    async def send(self, title, body):
        self.sent.append((title, body))
        return {}


def run(store, sales, now, rules=()):
    nt = Nt()
    r = asyncio.run(run_once(store, Fx([Fetched(sales, None, "商品", 9.9)]), nt, list(rules), item_ids=[ID],
                             now=now, sleep=lambda *_: asyncio.sleep(0)))
    return r, nt


def test_tier_crossing_alert_once_and_run_logged():
    s = Store()
    s.add_item(ID, "未分类", NOW)
    run(s, "已售9990", NOW)
    r, nt = run(s, "已售1.0万+", NOW + timedelta(hours=1))
    assert r.alerts == 1 and "进入取整区" in nt.sent[0][1]
    r, nt = run(s, "已售1.1万+", NOW + timedelta(hours=2))
    assert r.alerts == 0 and nt.sent == []
    runs = s.recent_runs()
    assert len(runs) == 3 and runs[0]["fetched"] == 1
    assert [a["rule"] for a in s.recent_alerts()] == ["进入取整区"]


class FakeCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def fetch(self, item_id):
        return Fetched("已售100", None, "测试商品", 9.9)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "Fetcher", FakeCtx)
    monkeypatch.setenv("XHS_NO_SCHEDULER", "1")
    with TestClient(app_module.create_app(":memory:")) as c:
        yield c


def _wait_fetched(client):
    for _ in range(40):
        items = client.get("/api/items").json()["items"]
        if items and all(i["sales"] for i in items):
            return items
        time.sleep(0.1)
    raise AssertionError("抓取超时")


def test_bulk_dashboard_logs_and_selected_run(client):
    a, b = ID, "691dd78095624300011c420b"
    client.post("/api/items", json={"text": f"{a}\n{b}"})
    _wait_fetched(client)
    assert client.post("/api/items/bulk", json={"ids": [a], "action": "category", "category": "教资"}).json() == {"changed": 1}
    assert client.get("/api/categories").json() == ["教资", "未分类"]
    d = client.get("/api/dashboard").json()
    assert d["items"] == 2 and d["categories"] == 2 and d["paused_until"] is None and len(d["runs"]) >= 1
    assert client.post("/api/run", json={"ids": [a]}).json() == {"started": True}
    logs = client.get("/api/logs").json()
    assert {"runs", "alerts", "failures"} == logs.keys()
    assert client.post("/api/items/bulk", json={"ids": [a, b], "action": "delete"}).json() == {"changed": 2}
    assert client.post("/api/items/bulk", json={"ids": [a], "action": "bad"}).status_code == 400
