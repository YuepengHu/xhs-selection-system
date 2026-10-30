import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from xhs_monitor import app as app_module
from xhs_monitor.fetcher import Fetched, extract_fields
from xhs_monitor.increments import Snap, daily_deltas
from xhs_monitor.runner import run_once
from xhs_monitor.store import Store

ID = "67ed05f404afe300019465be"
SHOP = "66f59ab9a9bf3400163b0b7d"
T0 = datetime(2026, 10, 30, 3, 1, tzinfo=timezone.utc)


class Fx:
    def __init__(self, items):
        self.items = list(items)

    async def fetch(self, item_id):
        return self.items.pop(0)


class Nt:
    async def send(self, *a):
        return {}


def f(shop_sales, sales="已售100"):
    return Fetched(sales, shop_sales, "商品", 9.9, SHOP, "优学资料屋的店", "粉丝数 1821")


def run(store, fetched, now):
    asyncio.run(run_once(store, Fx(fetched), Nt(), item_ids=[ID], now=now, sleep=lambda *_: asyncio.sleep(0)))


def test_extract_fields_reads_seller_block():
    payload = {"data": {"template_data": '[{"sellerH5": {"id": "%s", "name": "优学资料屋的店", '
                        '"fansAmount": "粉丝数 1821", "salesVolume": "已售 3.8万"}}]' % SHOP}}
    out = extract_fields(payload)
    assert (out["shop_id"], out["shop_name"], out["fans"], out["shop_sales"]) == (
        SHOP, "优学资料屋的店", "粉丝数 1821", "已售 3.8万")


def test_shop_snapshot_recorded_once_per_local_day():
    s = Store()
    s.add_item(ID, "未分类", T0)
    run(s, [f("已售 3.8万")], T0)
    run(s, [f("已售 3.8万")], T0 + timedelta(hours=1))
    assert len(s.shop_snapshots(SHOP)) == 1
    run(s, [f("已售 3.9万")], T0 + timedelta(days=1))
    snaps = s.shop_snapshots(SHOP)
    assert [x.low for x in snaps] == [38000, 39000] and snaps[0].step == 1000
    shop = s.list_shops()[0]
    assert shop["name"] == "优学资料屋的店" and shop["item_count"] == 1 and shop["sales"] == "已售 3.9万"
    assert s.get_item(ID).shop_id == SHOP


def test_daily_deltas_windows_and_rounding_bounds():
    now = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)
    snaps = [Snap(now - timedelta(days=d), low, 1000) for d, low in [(35, 30000), (8, 36000), (1, 38000), (0, 39000)]]
    d = daily_deltas(snaps, now)
    assert (d.day.lo, d.day.hi) == (1000 - 999, 1000 + 999)
    assert d.d7.lo == 3000 - 999 and d.d30.lo == 9000 - 999


def test_daily_deltas_none_when_history_short_or_gap_large():
    now = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)
    assert daily_deltas([Snap(now, 100, 1)], now).day is None
    d = daily_deltas([Snap(now - timedelta(days=5), 100, 1), Snap(now, 120, 1)], now)
    assert d.day is None and d.d7 is None


class FakeCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def fetch(self, item_id):
        return f("已售 3.8万")


def test_shops_api(monkeypatch):
    monkeypatch.setattr(app_module, "Fetcher", FakeCtx)
    monkeypatch.setenv("XHS_NO_SCHEDULER", "1")
    with TestClient(app_module.create_app(":memory:")) as c:
        c.post("/api/items", json={"text": ID})
        for _ in range(40):
            shops = c.get("/api/shops").json()["shops"]
            if shops:
                break
            time.sleep(0.1)
        assert shops[0]["name"] == "优学资料屋的店" and shops[0]["sales"] == "已售 3.8万"
        assert shops[0]["day"] is None
        items = c.get("/api/items?q=优学资料屋").json()["items"]
        assert items and items[0]["shop_name"] == "优学资料屋的店"
        assert len(c.get(f"/api/shops/{SHOP}/history").json()) == 1
        assert c.delete(f"/api/shops/{SHOP}").status_code == 200
        assert c.delete(f"/api/shops/{SHOP}").status_code == 404
