import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from xhs_collector.collector import Abort, Control, Record
from xhs_collector.service import CollectorService, ParamError, parse_params
from xhs_monitor import app as app_module
from xhs_monitor import collector_api
from xhs_monitor.fetcher import Fetched
from xhs_monitor.increments import TZ
from xhs_monitor.runner import due_ids
from xhs_monitor.stats import daily_totals
from xhs_monitor.store import Store

ID = "67ed05f404afe300019465be"
NOW = datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)


def rec(i, shop=None):
    return Record("关键词:教资", f"{i:024x}", f"商品{i}", 9.9, "已售100", shop, None, "", "", None, None, "2026-10-30T10:00:00")


class FakeAdb:
    def __init__(self, serial=None):
        self.serial = serial

    def shell(self, cmd):
        return "30000\n" if cmd.startswith("settings get") else ""

    def screen_height(self):
        return 2772


class FakeCollector:
    mode = "ok"

    def __init__(self, adb, cfg, control=None):
        self.control = control or Control(cfg.out_dir)

    def run(self, kw, filters, cap, on_record=None):
        if FakeCollector.mode == "abort":
            raise Abort("出现验证或风控提示：安全验证")
        if FakeCollector.mode == "block":
            while True:
                self.control.check()
                time.sleep(0.02)
        for i in (1, 2, 3):
            on_record(rec(i))
        return []

    run_shop = run_shop_name = run_keyword_shops = lambda self, *a, **k: self.run("x", None, None, a[-1] if callable(a[-1]) else k.get("on_record"))


@pytest.fixture(autouse=True)
def _reset_mode():
    FakeCollector.mode = "ok"


def wait_state(get, states, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        s = get()
        if s["state"] in states:
            return s
        time.sleep(0.02)
    raise AssertionError(f"状态没有变成 {states}: {get()['state']}")


def test_parse_params_defaults_and_validation():
    p = parse_params({"mode": "search", "keywords": ["教资"]})
    assert (p.limit, p.daily_cap, p.push, p.instance) == (20, 100, True, "Ⅱ·小红书")
    for bad in [{"mode": "x"}, {"mode": "search"}, {"mode": "shop"}, {"mode": "shop", "shop_ids": ["abc"]},
                {"mode": "search", "keywords": ["a"], "limit": 0}, {"mode": "search", "keywords": ["a"], "sort": "乱"},
                {"mode": "search", "keywords": ["a"], "min_price": "x"}, {"mode": "search", "keywords": ["a" * 80]}]:
        with pytest.raises(ParamError):
            parse_params(bad)
    p = parse_params({"mode": "shop", "shop_ids": ["a" * 24], "new_days": "7", "min_price": "1.5"})
    assert p.new_days == 7 and p.min_price == 1.5


def test_service_runs_saves_files_and_calls_on_finish(tmp_path):
    svc = CollectorService(tmp_path, adb_factory=FakeAdb, collector_factory=FakeCollector)
    got = []
    svc.start(parse_params({"mode": "search", "keywords": ["教资"], "category": "教资"}),
              on_finish=lambda records, cat: got.append(([r.item_id for r in records], cat)))
    s = wait_state(svc.status, {"done"})
    assert s["count"] == 3 and len(s["files"]) == 2 and all((tmp_path / f.split("/")[-1]).exists() for f in s["files"])
    assert got == [([f"{i:024x}" for i in (1, 2, 3)], "教资")]
    assert any("完成" in l for l in s["log"]) and not (tmp_path / "awake.json").exists()


def test_service_reports_error_and_allows_restart(tmp_path):
    svc = CollectorService(tmp_path, adb_factory=FakeAdb, collector_factory=FakeCollector)
    FakeCollector.mode = "abort"
    svc.start(parse_params({"mode": "search", "keywords": ["教资"]}))
    s = wait_state(svc.status, {"error"})
    assert "安全验证" in s["error"]
    FakeCollector.mode = "ok"
    svc.start(parse_params({"mode": "search", "keywords": ["教资"]}))
    wait_state(svc.status, {"done"})


def test_service_stop_and_single_job_rule(tmp_path):
    svc = CollectorService(tmp_path, adb_factory=FakeAdb, collector_factory=FakeCollector)
    FakeCollector.mode = "block"
    svc.start(parse_params({"mode": "search", "keywords": ["教资"]}))
    with pytest.raises(RuntimeError):
        svc.start(parse_params({"mode": "search", "keywords": ["教资"]}))
    svc.pause()
    assert svc.status()["paused"]
    svc.resume()
    svc.stop()
    wait_state(svc.status, {"stopped"})


@pytest.fixture
def client(monkeypatch, tmp_path):
    class FakeFetch:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def fetch(self, item_id):
            return Fetched("已售100", None, "商品", 9.9)

    monkeypatch.setattr(app_module, "Fetcher", FakeFetch)
    monkeypatch.setattr(collector_api, "CollectorService",
                        lambda d: CollectorService(d, adb_factory=FakeAdb, collector_factory=FakeCollector))
    monkeypatch.setenv("XHS_NO_SCHEDULER", "1")
    monkeypatch.setenv("XHS_COLLECTOR_DIR", str(tmp_path / "col"))
    monkeypatch.delenv("XHS_API_TOKEN", raising=False)
    with TestClient(app_module.create_app(":memory:")) as c:
        yield c


def test_collector_api_start_adds_items_to_monitor(client):
    assert client.get("/api/collector/status").json()["state"] == "idle"
    assert client.post("/api/collector/start", json={"mode": "search"}).status_code == 400
    r = client.post("/api/collector/start", json={"mode": "search", "keywords": ["教资"], "category": "教资"})
    assert r.json() == {"started": True}
    wait_state(lambda: client.get("/api/collector/status").json(), {"done"})
    for _ in range(50):
        items = client.get("/api/items").json()["items"]
        if len(items) == 3 and all(i["sales"] for i in items):
            break
        time.sleep(0.05)
    assert len(items) == 3 and {i["category"] for i in items} == {"教资"}
    assert len(client.get("/api/collector/records").json()) == 3
    assert client.get("/api/collector/download?kind=csv").status_code == 200
    assert client.get("/api/collector/download?kind=exe").status_code == 400


def test_collector_api_conflict_and_control(client):
    FakeCollector.mode = "block"
    assert client.post("/api/collector/start", json={"mode": "search", "keywords": ["a"]}).status_code == 200
    assert client.post("/api/collector/start", json={"mode": "search", "keywords": ["a"]}).status_code == 409
    assert client.post("/api/collector/control/pause").json() == {"ok": True}
    assert client.post("/api/collector/control/bogus").status_code == 404
    client.post("/api/collector/control/stop")
    wait_state(lambda: client.get("/api/collector/status").json(), {"stopped"})


def test_collector_api_bad_serial_rejected(client):
    assert client.get("/api/collector/screenshot?serial=a;rm -rf").status_code == 400
    r = client.post("/api/collector/start", json={"mode": "search", "keywords": ["a"], "serial": "x y"})
    assert r.status_code == 400


def test_token_auth(monkeypatch):
    monkeypatch.setenv("XHS_NO_SCHEDULER", "1")
    monkeypatch.setenv("XHS_API_TOKEN", "s3cret")
    with TestClient(app_module.create_app(":memory:")) as c:
        assert c.get("/").status_code == 200
        assert c.get("/api/auth/required").json() == {"required": True}
        assert c.get("/api/items").status_code == 401
        assert c.get("/api/items", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/items", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/items", headers={"X-API-Token": "s3cret"}).status_code == 200


def test_paused_items_are_not_due_and_bulk_pause_resume(client):
    s = Store()
    s.add_item(ID, "未分类", NOW)
    assert due_ids(s, NOW) == [ID]
    s.set_status(ID, "paused")
    assert due_ids(s, NOW) == []
    client.post("/api/items", json={"text": ID})
    for _ in range(40):
        items = client.get("/api/items").json()["items"]
        if items and items[0]["sales"]:
            break
        time.sleep(0.05)
    assert items[0]["status"] == "active"
    assert client.post("/api/items/bulk", json={"ids": [ID], "action": "pause"}).json() == {"changed": 1}
    assert client.get("/api/items").json()["items"][0]["status"] == "paused"
    assert client.post("/api/items/bulk", json={"ids": [ID], "action": "resume"}).json() == {"changed": 1}


def test_daily_totals_sum_exact_and_ignore_gaps():
    s = Store()
    for item in (ID, "b" * 24):
        s.add_item(item, "未分类", NOW)
    day = lambda d, h=12: (NOW.astimezone(TZ) - timedelta(days=d)).replace(hour=h, minute=0)
    for d, low in [(3, 100), (2, 110), (1, 125), (0, 130)]:
        s.add_snapshot(ID, day(d), raw=f"已售{low}", low=low, step=1, price=1, error=None)
    for d, low in [(3, 50), (1, 60), (0, 61)]:  # 缺了前天
        s.add_snapshot("b" * 24, day(d), raw=f"已售{low}", low=low, step=1, price=1, error=None)
    out = {r["date"]: r for r in daily_totals(s, NOW, 4)}
    today, d1, d2 = (NOW.astimezone(TZ).date() - timedelta(days=k) for k in (0, 1, 2))
    assert out[today.isoformat()] == {"date": today.isoformat(), "total": 5 + 1, "growing": 2}
    assert out[d1.isoformat()]["total"] == 15 and out[d1.isoformat()]["growing"] == 1
    assert out[d2.isoformat()]["total"] == 10


def test_stats_api_shape(client):
    client.post("/api/items", json={"text": ID, "category": "教资"})
    for _ in range(40):
        s = client.get("/api/stats?days=7").json()
        if s["categories"]:
            break
        time.sleep(0.05)
    assert len(s["daily"]) == 7 and s["categories"][0]["category"] == "教资" and s["categories"][0]["items"] == 1
    assert "top7" in s
