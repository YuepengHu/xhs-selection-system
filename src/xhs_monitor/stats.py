from collections import defaultdict
from datetime import datetime, timedelta

from .increments import TZ, Snap, delta
from .store import Store


def daily_totals(store: Store, now: datetime, days: int = 14) -> list[dict]:
    """每个本地自然日全部商品的销量增量合计（用增量下界，只统计有前一天数据的商品）。"""
    first = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    last_of_day: dict[str, dict[str, Snap]] = defaultdict(dict)
    for item_id, ts, low, step in store.snapshots_since(first):
        last_of_day[item_id][ts.astimezone(TZ).date().isoformat()] = Snap(ts, low, step)
    total: dict[str, int] = defaultdict(int)
    growing: dict[str, int] = defaultdict(int)
    for per_day in last_of_day.values():
        for d, snap in per_day.items():
            prev = (datetime.fromisoformat(d) - timedelta(days=1)).date().isoformat()
            if prev in per_day:
                lo = max(delta(per_day[prev], snap).lo, 0)
                total[d] += lo
                growing[d] += lo > 0
    start = now.astimezone(TZ).date() - timedelta(days=days - 1)
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "total": total.get((start + timedelta(days=i)).isoformat(), 0),
             "growing": growing.get((start + timedelta(days=i)).isoformat(), 0)} for i in range(days)]
