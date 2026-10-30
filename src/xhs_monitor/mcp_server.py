"""把销量监控数据开放给 AI 助手的 MCP 服务（stdio）。通过本机 HTTP 接口读数据，所以监控服务需要先运行。

环境变量：XHS_MONITOR_URL（默认 http://127.0.0.1:8765）、XHS_API_TOKEN（监控服务开启了口令时需要）。
"""
import os

import httpx

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

server = _Server("xhs-monitor", instructions=(
    "小红书商品销量监控。增量里的 lo 是下界、hi 是上界；exact 为 false 表示销量被取整，真实增量只能确定在 lo 到 hi 之间。"))


def _client() -> httpx.Client:
    headers = {"Authorization": f"Bearer {os.environ['XHS_API_TOKEN']}"} if os.getenv("XHS_API_TOKEN") else {}
    return httpx.Client(base_url=os.getenv("XHS_MONITOR_URL", "http://127.0.0.1:8765"), headers=headers, timeout=30)


def _call(method: str, path: str, **kw):
    with _client() as c:
        r = c.request(method, path, **kw)
    if r.status_code == 401:
        raise RuntimeError("监控服务需要口令，请设置环境变量 XHS_API_TOKEN")
    r.raise_for_status()
    return r.json()


def _d(x):
    return None if not x else {"lo": x["lo"], "hi": x["hi"], "exact": x["exact"]}


@server.tool()
def list_items(query: str = "", category: str = "", sort: str = "today", limit: int = 20) -> list[dict]:
    """列出监控的商品，含各时间窗口的销量增量。sort 可选 today、yesterday、hour、d7、d30。"""
    rows = _call("GET", "/api/items", params={"q": query, "category": category, "sort": sort})["items"]
    keys = ("id", "title", "price", "category", "shop_name", "sales", "status")
    return [{**{k: r[k] for k in keys}, **{w: _d(r[w]) for w in ("hour", "today", "yesterday", "d7", "d30")}}
            for r in rows[:max(1, min(limit, 100))]]


@server.tool()
def get_item_history(item_id: str, limit: int = 100) -> list[dict]:
    """某个商品的抓取历史（最新在前），含销量原文、价格和失败原因。"""
    return _call("GET", f"/api/items/{item_id}/history")[:max(1, min(limit, 500))]


@server.tool()
def list_shops(sort: str = "day", limit: int = 20) -> list[dict]:
    """列出由商品派生出的店铺及日、7 天、30 天销量增量。sort 可选 day、d7、d30。"""
    rows = _call("GET", "/api/shops", params={"sort": sort})["shops"]
    return [{"id": r["id"], "name": r["name"], "fans": r["fans"], "items": r["item_count"], "sales": r["sales"],
             **{w: _d(r[w]) for w in ("day", "d7", "d30")}} for r in rows[:max(1, min(limit, 100))]]


@server.tool()
def get_dashboard() -> dict:
    """汇总：商品数、店铺数、待抓取数、今日增量 Top 10、最近提醒和抓取记录。"""
    return _call("GET", "/api/dashboard")


@server.tool()
def get_stats(days: int = 14) -> dict:
    """每日全部商品增量合计趋势、分类汇总和 7 天增量 Top 10。"""
    return _call("GET", "/api/stats", params={"days": days})


@server.tool()
def get_recent_alerts(limit: int = 20) -> list[dict]:
    """最近发出的销量异动提醒。"""
    return _call("GET", "/api/logs")["alerts"][:max(1, min(limit, 100))]


@server.tool()
def add_items(text: str, category: str = "未分类") -> dict:
    """把商品加入监控。text 可以是商品链接、小红书分享短链或 24 位商品 ID，多个用换行分隔。"""
    return _call("POST", "/api/items", json={"text": text, "category": category})


@server.tool()
def run_fetch(item_ids: list[str]) -> dict:
    """立即抓取指定商品的最新销量（异步，结果稍后在 list_items 里看到）。"""
    return _call("POST", "/api/run", json={"ids": item_ids})


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
