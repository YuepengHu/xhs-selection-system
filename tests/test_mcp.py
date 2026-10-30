import asyncio

import httpx
import pytest

from xhs_monitor import mcp_server

DELTA = {"lo": 5, "hi": 5, "exact": True}


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if request.headers.get("authorization") != "Bearer tok":
        return httpx.Response(401, json={"detail": "需要访问口令"})
    if p == "/api/items" and request.method == "GET":
        row = {"id": "a" * 24, "title": "商品", "price": 2.99, "category": "教资", "shop_name": "店", "sales": "已售5",
               "status": "active", "hour": DELTA, "today": DELTA, "yesterday": None, "d7": None, "d30": None,
               "extra": "不应出现"}
        return httpx.Response(200, json={"items": [row] * 3, "total": 3})
    if p.endswith("/history"):
        return httpx.Response(200, json=[{"ts": "t", "ok": 1}] * 5)
    if p == "/api/shops":
        return httpx.Response(200, json={"shops": [{"id": "s", "name": "店", "fans": "粉丝数 1", "item_count": 2,
                                                    "sales": "已售9486", "day": DELTA, "d7": None, "d30": None}]})
    if p == "/api/logs":
        return httpx.Response(200, json={"alerts": [{"rule": "r"}] * 4, "runs": [], "failures": []})
    if p in ("/api/dashboard", "/api/stats"):
        return httpx.Response(200, json={"ok": p})
    if p == "/api/items" and request.method == "POST":
        return httpx.Response(200, json={"added": ["x"]})
    if p == "/api/run":
        return httpx.Response(200, json={"started": True})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", lambda: httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer tok"}))


def test_tools_are_registered():
    names = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert names == {"list_items", "get_item_history", "list_shops", "get_dashboard", "get_stats",
                     "get_recent_alerts", "add_items", "run_fetch"}


def test_list_items_trims_fields_and_limit():
    out = mcp_server.list_items(limit=2)
    assert len(out) == 2 and "extra" not in out[0] and out[0]["today"] == DELTA and out[0]["d7"] is None


def test_other_tools_shape_and_limits():
    assert len(mcp_server.get_item_history("a" * 24, limit=3)) == 3
    assert mcp_server.list_shops()[0] == {"id": "s", "name": "店", "fans": "粉丝数 1", "items": 2, "sales": "已售9486",
                                          "day": DELTA, "d7": None, "d30": None}
    assert len(mcp_server.get_recent_alerts(limit=2)) == 2
    assert mcp_server.get_dashboard() == {"ok": "/api/dashboard"}
    assert mcp_server.get_stats(7) == {"ok": "/api/stats"}
    assert mcp_server.add_items("x") == {"added": ["x"]}
    assert mcp_server.run_fetch(["x"]) == {"started": True}


def test_missing_token_gives_clear_error(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", lambda: httpx.Client(base_url="http://test",
                                                                      transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="XHS_API_TOKEN"):
        mcp_server.list_items()
