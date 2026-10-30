from dataclasses import dataclass

from .increments import Delta, Metrics

HOUR_COOLDOWN = 3
STREAK_COOLDOWN = 20


@dataclass(frozen=True)
class Rule:
    name: str
    min_hour_delta: int = 1
    min_price: float | None = None
    kind: str = "hour"
    days: int = 3
    min_daily: int = 1

    @property
    def cooldown_hours(self) -> int:
        return STREAK_COOLDOWN if self.kind == "streak" else HOUR_COOLDOWN


DEFAULT_RULES = [
    Rule("近1小时新增>=5", 5),
    Rule("价格>=29且近1小时新增>1", 2, 29.0),
]


def triggered(rules: list[Rule], metrics: Metrics, price: float | None,
              daily: list[Delta | None] | None = None) -> list[Rule]:
    """用增量下界判断，取整造成的不确定性不会产生误报。
    daily 是最近已完成的几天的日增量，最近的一天在前。"""
    out = []
    for r in rules:
        if r.min_price is not None and (price is None or price < r.min_price):
            continue
        if r.kind == "streak":
            days = (daily or [])[:r.days]
            if len(days) == r.days and all(d is not None and d.lo >= r.min_daily for d in days):
                out.append(r)
        elif metrics.hour is not None and metrics.hour.lo >= r.min_hour_delta:
            out.append(r)
    return out
