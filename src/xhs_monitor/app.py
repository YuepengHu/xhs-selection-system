import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import collector_api
from . import settings as cfg
from .fetcher import Fetcher
from .increments import TZ, Delta, Snap, compute, daily_deltas, delta
from .notify import Notifier
from .resolve import resolve_items
from .runner import HISTORY_WINDOW, due_ids, run_once
from .stats import daily_totals
from .store import Store

log = logging.getLogger("xhs_monitor")
STATIC = Path(__file__).parent / "static"
SORT_KEYS = {"hour", "today", "yesterday", "d7", "d30"}
POLL_MINUTES = 5


class AddItems(BaseModel):
    text: str
    category: str = "未分类"


class Category(BaseModel):
    category: str


def _delta(d: Delta | None):
    return None if d is None else {"lo": d.lo, "hi": d.hi, "exact": d.exact}


def _long_delta(store: Store, item_id: str, latest: Snap | None, now: datetime, days: int) -> Delta | None:
    if latest is None:
        return None
    base = store.snapshot_at_or_before(item_id, now - timedelta(days=days))
    return delta(base, latest) if base and base.ts < latest.ts else None


def create_app(db_path: str | None = None) -> FastAPI:
    state: dict = {}
    run_lock = asyncio.Lock()

    def current_settings() -> cfg.Settings:
        return cfg.load(state["store"])

    async def run_job(item_ids: list[str] | None = None, force: bool = False):
        store, s, now = state["store"], current_settings(), datetime.now(timezone.utc)
        paused = store.paused_until()
        if paused and paused > now:
            return {"skipped": "已暂停"}
        if force:
            item_ids = [i.id for i in store.list_items()]
        ids = item_ids if item_ids is not None else due_ids(store, now, (s.active_start, s.active_end))
        if not ids:
            return {"skipped": "没有到期的商品"}
        if run_lock.locked():
            return {"skipped": "上一轮仍在运行"}
        async with run_lock:
            async with Fetcher() as fetcher:
                r = await run_once(store, fetcher, state["notifier"], s.rules, item_ids=ids)
            log.info("本轮抓取完成: %s", r)
            return r.__dict__

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path = db_path or os.getenv("XHS_DB", "data/monitor.db")
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        state["store"] = Store(path)
        state["client"] = httpx.AsyncClient(timeout=15)
        state["notifier"] = Notifier(state["client"], current_settings)
        sched = AsyncIOScheduler(timezone=TZ)
        sched.add_job(run_job, "interval", minutes=POLL_MINUTES, max_instances=1, coalesce=True,
                      next_run_time=datetime.now(TZ) + timedelta(seconds=10))
        if os.getenv("XHS_NO_SCHEDULER") != "1":
            sched.start()
        yield
        if sched.running:
            sched.shutdown(wait=False)
        await state["client"].aclose()

    app = FastAPI(title="小红书销量监控", lifespan=lifespan)
    token = os.getenv("XHS_API_TOKEN", "")

    @app.middleware("http")
    async def require_token(request, call_next):
        path = request.url.path
        if token and path.startswith("/api/") and path != "/api/auth/required":
            auth = request.headers.get("authorization", "")
            got = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-api-token", "")
            if not hmac.compare_digest(got.encode(), token.encode()):
                return JSONResponse({"detail": "需要访问口令"}, status_code=401)
        return await call_next(request)

    @app.get("/api/auth/required")
    def auth_required():
        return {"required": bool(token)}

    async def add_ids(ids: list[str], category: str) -> list[str]:
        now = datetime.now(timezone.utc)
        new = [i for i in ids if state["store"].add_item(i, category, now)]
        if new:
            asyncio.create_task(run_job(new))
        return new

    collector_api.install(app, state, add_ids, Path(os.getenv("XHS_COLLECTOR_DIR", "data/collector")))

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/items")
    async def add_items(body: AddItems):
        ids, bad = await resolve_items(body.text, state["client"])
        now = datetime.now(timezone.utc)
        new = [i for i in ids if state["store"].add_item(i, body.category, now)]
        if new:
            asyncio.create_task(run_job(new))
        return {"added": new, "existing": [i for i in ids if i not in new], "unrecognized": bad}

    def item_rows(q: str = "", category: str = "") -> list[dict]:
        store, now = state["store"], datetime.now(timezone.utc)
        shop_names = {s["id"]: s["name"] for s in store.list_shops()}
        rows = []
        for it in store.list_items():
            if category and it.category != category:
                continue
            if q and q.lower() not in f"{it.id} {it.title or ''} {shop_names.get(it.shop_id) or ''}".lower():
                continue
            snaps = store.snapshots(it.id, since=now - HISTORY_WINDOW)
            m = compute(snaps, now)
            latest = snaps[-1] if snaps else store.last_snapshot(it.id)
            rows.append({
                "id": it.id, "title": it.title, "price": it.price, "category": it.category,
                "shop_id": it.shop_id, "shop_name": shop_names.get(it.shop_id), "status": it.status,
                "shop_sales": it.shop_sales, "sales": store.last_raw(it.id),
                "last_fetch_at": it.last_fetch_at.isoformat() if it.last_fetch_at else None,
                "hour": _delta(m.hour), "today": _delta(m.today), "yesterday": _delta(m.yesterday),
                "d7": _delta(_long_delta(store, it.id, latest, now, 7)),
                "d30": _delta(_long_delta(store, it.id, latest, now, 30)),
            })
        return rows

    def sort_rows(rows: list[dict], sort: str, desc: bool, keys: set[str]) -> None:
        if sort in keys:
            rows.sort(key=lambda r: (r[sort] is not None, r[sort]["lo"] if r[sort] else 0), reverse=desc)

    @app.get("/api/items")
    def list_items(q: str = "", category: str = "", sort: str = "today", desc: bool = True):
        rows = item_rows(q, category)
        sort_rows(rows, sort, desc, SORT_KEYS)
        return {"items": rows, "total": len(rows)}

    @app.post("/api/items/bulk")
    def bulk(body: dict = Body(...)):
        ids, action = body.get("ids") or [], body.get("action")
        if not isinstance(ids, list) or action not in ("delete", "category", "pause", "resume"):
            raise HTTPException(400, "参数不合法")
        store, n = state["store"], 0
        for i in ids:
            if action == "delete":
                n += store.delete_item(str(i))
            elif action in ("pause", "resume"):
                n += store.set_status(str(i), "paused" if action == "pause" else "active")
            elif store.get_item(str(i)):
                store.set_category(str(i), str(body.get("category") or "未分类"))
                n += 1
        return {"changed": n}

    @app.get("/api/dashboard")
    def dashboard():
        store, now = state["store"], datetime.now(timezone.utc)
        s = current_settings()
        rows = item_rows()
        top = [r for r in rows if r["today"]]
        sort_rows(top, "today", True, {"today"})
        until = store.paused_until()
        return {
            "items": len(rows), "shops": len(store.list_shops()),
            "categories": len({r["category"] for r in rows}),
            "due": len(due_ids(store, now, (s.active_start, s.active_end))),
            "paused_until": until.isoformat() if until and until > now else None,
            "top_today": [{k: r[k] for k in ("id", "title", "price", "sales", "today", "hour")} for r in top[:10]],
            "alerts": store.recent_alerts(10), "runs": store.recent_runs(5),
        }

    @app.get("/api/stats")
    def stats(days: int = 14):
        store, now = state["store"], datetime.now(timezone.utc)
        rows = item_rows()
        cats: dict[str, dict] = {}
        for r in rows:
            c = cats.setdefault(r["category"], {"category": r["category"], "items": 0, "today": 0, "d7": 0})
            c["items"] += 1
            c["today"] += max((r["today"] or {}).get("lo", 0), 0)
            c["d7"] += max((r["d7"] or {}).get("lo", 0), 0)
        top7 = [r for r in rows if r["d7"]]
        sort_rows(top7, "d7", True, {"d7"})
        return {"daily": daily_totals(store, now, max(2, min(days, 60))),
                "categories": sorted(cats.values(), key=lambda c: -c["d7"]),
                "top7": [{k: r[k] for k in ("id", "title", "category", "sales", "d7", "today")} for r in top7[:10]]}

    @app.get("/api/logs")
    def logs():
        store = state["store"]
        return {"runs": store.recent_runs(100), "alerts": store.recent_alerts(100),
                "failures": store.recent_failures(100)}

    @app.get("/api/shops")
    def list_shops(sort: str = "day", desc: bool = True):
        store, now = state["store"], datetime.now(timezone.utc)
        rows = []
        for s in store.list_shops():
            d = daily_deltas(store.shop_snapshots(s["id"]), now)
            rows.append({**s, "day": _delta(d.day), "d7": _delta(d.d7), "d30": _delta(d.d30)})
        if sort in {"day", "d7", "d30"}:
            rows.sort(key=lambda r: (r[sort] is not None, r[sort]["lo"] if r[sort] else 0), reverse=desc)
        return {"shops": rows, "total": len(rows)}

    @app.get("/api/shops/{shop_id}/history")
    def shop_history(shop_id: str):
        return state["store"].shop_history(shop_id)

    @app.delete("/api/shops/{shop_id}")
    def delete_shop(shop_id: str):
        if not state["store"].delete_shop(shop_id):
            raise HTTPException(404, "店铺不存在")
        return {"ok": True}

    @app.get("/api/categories")
    def categories():
        return sorted({i.category for i in state["store"].list_items()})

    @app.get("/api/categories/stats")
    def category_stats():
        return state["store"].category_counts()

    @app.put("/api/categories/{name}")
    def rename_category(name: str, body: Category):
        new = body.category.strip()
        if not new:
            raise HTTPException(400, "分类名不能为空")
        return {"changed": state["store"].rename_category(name, new)}

    @app.delete("/api/categories/{name}")
    def delete_category(name: str):
        """删除分类不删商品，商品归入"未分类"。"""
        return {"changed": state["store"].rename_category(name, "未分类")}

    @app.get("/api/items/{item_id}/history")
    def history(item_id: str):
        if state["store"].get_item(item_id) is None:
            raise HTTPException(404, "商品不存在")
        return state["store"].history(item_id)

    @app.put("/api/items/{item_id}/category")
    def set_category(item_id: str, body: Category):
        if state["store"].get_item(item_id) is None:
            raise HTTPException(404, "商品不存在")
        state["store"].set_category(item_id, body.category)
        return {"ok": True}

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: str):
        if not state["store"].delete_item(item_id):
            raise HTTPException(404, "商品不存在")
        return {"ok": True}

    @app.post("/api/run")
    async def run_now(body: dict | None = Body(default=None), force: bool = False):
        ids = (body or {}).get("ids")
        asyncio.create_task(run_job(item_ids=ids if isinstance(ids, list) else None, force=force))
        return {"started": True}

    @app.get("/api/status")
    def status():
        until = state["store"].paused_until()
        paused = bool(until and until > datetime.now(timezone.utc))
        return {"paused": paused, "paused_until": until.isoformat() if until else None,
                "running": run_lock.locked()}

    @app.post("/api/resume")
    def resume():
        state["store"].resume()
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings():
        return cfg.public(current_settings())

    @app.put("/api/settings")
    def put_settings(patch: dict = Body(...)):
        try:
            return cfg.public(cfg.update(state["store"], patch))
        except cfg.SettingsError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/notify/test")
    async def notify_test():
        result = await state["notifier"].send("小红书监控测试消息", "这是一条测试消息，收到说明推送配置可用。")
        return {"configured": bool(result), "result": result}

    return app


app = create_app()
