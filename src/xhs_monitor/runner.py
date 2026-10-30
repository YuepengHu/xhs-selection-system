import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .alerts import DEFAULT_RULES, Rule, triggered
from .fetcher import FetchError, VerificationRequired
from .increments import TZ, compute, daily_series
from .parse import parse_sales
from .store import Store

log = logging.getLogger(__name__)
PAUSE_AFTER_RISK = timedelta(hours=6)
APPROX_REFRESH = timedelta(hours=20)
HISTORY_WINDOW = timedelta(days=3)
MAX_FAILS_PER_HOUR = 2
RETRY_GAP = timedelta(minutes=10)
DEFAULT_WINDOW = (10, 23)


@dataclass
class RunResult:
    fetched: int = 0
    failed: int = 0
    alerts: int = 0
    paused: bool = False
    skipped: str | None = None


def is_due(store: Store, item_id: str, now: datetime, window: tuple[int, int] = DEFAULT_WINDOW) -> bool:
    """每个本地整点小时槽内每个商品抓一次；错过的整点会在之后的轮询里补上。"""
    local = now.astimezone(TZ)
    attempts = store.recent_attempts(item_id, local.replace(minute=0, second=0, microsecond=0))
    fails = [t for t, ok in attempts if not ok]
    if len(fails) >= MAX_FAILS_PER_HOUR or (fails and now - max(fails) < RETRY_GAP):
        return False
    last = store.last_snapshot(item_id)
    if last is None:
        return True
    if not window[0] <= local.hour <= window[1]:
        return False
    if last.step > 1:
        return now - last.ts >= APPROX_REFRESH
    return not any(ok for _, ok in attempts)


def due_ids(store: Store, now: datetime, window: tuple[int, int] = DEFAULT_WINDOW) -> list[str]:
    return [i.id for i in store.list_items() if is_due(store, i.id, now, window)]


def _record_shop(store: Store, f, now: datetime) -> None:
    """店铺销量取自商品页，每个店铺每个本地自然日只记第一次。"""
    if not f.shop_id or not f.shop_sales:
        return
    sales = parse_sales(f.shop_sales)
    store.upsert_shop(f.shop_id, f.shop_name, f.fans, now)
    if sales is None:
        return
    today = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if not store.has_shop_snapshot_since(f.shop_id, today):
        store.add_shop_snapshot(f.shop_id, now, sales.raw, sales.low, sales.step)


def _delta_txt(d) -> str:
    if d is None:
        return "未知"
    return str(d.lo) if d.exact else f">={d.lo}"


def _fmt_alert(title: str | None, item_id: str, price, raw, metrics, rules: list[Rule]) -> str:
    return (f"- **{title or item_id}**  价格 {price if price is not None else '?'}  总销量 {raw}\n"
            f"  今日新增 {_delta_txt(metrics.today)}，近1小时新增 {_delta_txt(metrics.hour)}\n"
            f"  触发：{'、'.join(r.name for r in rules)}")


TIER_RULE = "进入取整区"
TIER_COOLDOWN = timedelta(days=365)


async def run_once(store: Store, fetcher, notifier, rules: list[Rule] = DEFAULT_RULES, *,
                   item_ids: list[str] | None = None, now: datetime | None = None,
                   window: tuple[int, int] = DEFAULT_WINDOW,
                   sleep=asyncio.sleep, jitter: tuple[float, float] = (1.5, 3.5)) -> RunResult:
    now = now or datetime.now(timezone.utc)
    result = RunResult()
    paused = store.paused_until()
    if paused and paused > now:
        result.skipped = f"已暂停至 {paused.isoformat()}"
        return result

    ids = item_ids if item_ids is not None else due_ids(store, now, window)
    hits: list[str] = []
    streak_days = max((r.days for r in rules if r.kind == "streak"), default=0)
    history = max(HISTORY_WINDOW, timedelta(days=streak_days + 2))

    for n, item_id in enumerate(ids):
        if n:
            await sleep(random.uniform(*jitter))
        t = now
        try:
            f = await fetcher.fetch(item_id)
        except VerificationRequired as e:
            store.pause(t + PAUSE_AFTER_RISK)
            result.paused = True
            await notifier.send("小红书监控已暂停", f"遇到验证或限流（{e}），已暂停 6 小时，请人工检查后再恢复。")
            break
        except FetchError as e:
            store.add_snapshot(item_id, t, raw=None, low=None, step=None, price=None, error=str(e))
            result.failed += 1
            continue

        sales = parse_sales(f.sales_text) if f.sales_text else None
        if sales is None:
            store.add_snapshot(item_id, t, raw=f.sales_text, low=None, step=None, price=f.price,
                               error="销量无法解析" if f.sales_text else "响应中没有销量")
            result.failed += 1
            continue

        prev = store.last_snapshot(item_id)
        store.add_snapshot(item_id, t, raw=sales.raw, low=sales.low, step=sales.step,
                           price=f.price, error=None)
        store.update_info(item_id, f.title, f.price, f.shop_sales, t, f.shop_id)
        _record_shop(store, f, t)
        result.fetched += 1

        item = store.get_item(item_id)
        snaps = store.snapshots(item_id, since=t - history)
        metrics = compute(snaps, t)
        daily = daily_series(snaps, t, streak_days) if streak_days else None
        fresh = [r for r in triggered(rules, metrics, item.price, daily)
                 if not store.alerted_since(item_id, r.name, t - timedelta(hours=r.cooldown_hours))]
        for r in fresh:
            store.record_alert(item_id, r.name, t)
        if fresh:
            hits.append(_fmt_alert(item.title, item_id, item.price, sales.raw, metrics, fresh))
        if prev and prev.step == 1 and sales.step > 1 and not store.alerted_since(item_id, TIER_RULE, t - TIER_COOLDOWN):
            store.record_alert(item_id, TIER_RULE, t)
            hits.append(f"- **{item.title or item_id}**  销量已显示为 {sales.raw}，之后只能看到取整值，"
                        f"小时级增量不可用，抓取频率降为每天一次\n  触发：{TIER_RULE}")

    result.alerts = len(hits)
    if ids:
        store.log_run(now, len(ids), result)
    if hits:
        await notifier.send(f"销量异动提醒（{len(hits)} 条）", "\n\n".join(hits))
    return result
