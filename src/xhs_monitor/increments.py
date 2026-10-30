from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
HOURLY_MAX_GAP = timedelta(hours=3)


@dataclass(frozen=True)
class Snap:
    ts: datetime
    low: int
    step: int

    @property
    def high(self) -> int:
        return self.low + self.step - 1


@dataclass(frozen=True)
class Delta:
    lo: int
    hi: int

    @property
    def exact(self) -> bool:
        return self.lo == self.hi


@dataclass(frozen=True)
class Metrics:
    hour: Delta | None
    today: Delta | None
    yesterday: Delta | None


def delta(a: Snap, b: Snap) -> Delta:
    return Delta(b.low - a.high, b.high - a.low)


def _at_or_before(snaps: list[Snap], ts: datetime) -> Snap | None:
    found = None
    for s in snaps:
        if s.ts <= ts:
            found = s
    return found


@dataclass(frozen=True)
class DailyDeltas:
    day: Delta | None
    d7: Delta | None
    d30: Delta | None


DAY_MAX_GAP = timedelta(days=3)
DAY_EDGE_TOLERANCE = timedelta(hours=24)


def daily_deltas(snaps: list[Snap], now: datetime) -> DailyDeltas:
    """按"每天一条"的快照序列算日、7 天、30 天增量；历史不够长的窗口返回 None。"""
    if len(snaps) < 2:
        return DailyDeltas(None, None, None)
    latest = snaps[-1]
    day = delta(snaps[-2], latest) if latest.ts - snaps[-2].ts <= DAY_MAX_GAP else None

    def window(days: int) -> Delta | None:
        base = _at_or_before(snaps, now - timedelta(days=days))
        return delta(base, latest) if base and base.ts < latest.ts else None

    return DailyDeltas(day, window(7), window(30))


def daily_series(snaps: list[Snap], now: datetime, n: int) -> list[Delta | None]:
    """最近 n 个已完成的本地自然日的日增量，昨天在前；缺数据的日子为 None。"""
    midnight = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[Delta | None] = []
    for k in range(1, n + 1):
        day_end, day_start = midnight - timedelta(days=k - 1), midnight - timedelta(days=k)
        end, start = _at_or_before(snaps, day_end), _at_or_before(snaps, day_start)
        usable = (start and end and start is not end
                  and day_end - end.ts <= DAY_EDGE_TOLERANCE and day_start - start.ts <= DAY_EDGE_TOLERANCE)
        out.append(delta(start, end) if usable else None)
    return out


def compute(snaps: list[Snap], now: datetime) -> Metrics:
    if not snaps:
        return Metrics(None, None, None)
    latest = snaps[-1]
    midnight = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    hour = None
    if len(snaps) >= 2 and latest.ts - snaps[-2].ts <= HOURLY_MAX_GAP:
        hour = delta(snaps[-2], latest)

    end_yesterday = _at_or_before(snaps, midnight)
    base_today = end_yesterday or snaps[0]
    today = delta(base_today, latest) if base_today is not latest else None

    start_yesterday = _at_or_before(snaps, midnight - timedelta(days=1))
    yesterday = None
    if start_yesterday and end_yesterday and start_yesterday is not end_yesterday:
        yesterday = delta(start_yesterday, end_yesterday)

    return Metrics(hour, today, yesterday)
