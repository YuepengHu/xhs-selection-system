from datetime import datetime, timedelta, timezone

from xhs_monitor.alerts import DEFAULT_RULES, triggered
from xhs_monitor.increments import TZ, Snap, compute, delta

UTC = timezone.utc


def snap(ts, low, step=1):
    return Snap(ts.astimezone(UTC), low, step)


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def test_exact_delta():
    a, b = snap(local(2026, 10, 30, 10), 1000), snap(local(2026, 10, 30, 11), 1006)
    d = delta(a, b)
    assert d.exact and d.lo == 6


def test_cross_into_rounded_gives_lower_bound_only():
    a = snap(local(2026, 10, 30, 10), 9987)
    b = snap(local(2026, 10, 30, 11), 10000, 1000)
    d = delta(a, b)
    assert (d.lo, d.hi) == (13, 1012) and not d.exact


def test_rounded_to_rounded_can_have_zero_lower_bound():
    a = snap(local(2026, 10, 30, 10), 18000, 1000)
    b = snap(local(2026, 10, 30, 11), 18000, 1000)
    d = delta(a, b)
    assert d.lo < 0 < d.hi


def test_hour_today_yesterday():
    snaps = [
        snap(local(2026, 10, 28, 23), 100),
        snap(local(2026, 10, 29, 23), 130),
        snap(local(2026, 10, 30, 10), 140),
        snap(local(2026, 10, 30, 11), 146),
    ]
    m = compute(snaps, local(2026, 10, 30, 11, 5))
    assert m.hour.lo == 6
    assert m.today.lo == 16
    assert m.yesterday.lo == 30


def test_hour_missing_when_gap_too_large():
    snaps = [snap(local(2026, 10, 30, 10), 1), snap(local(2026, 10, 30, 18), 50)]
    assert compute(snaps, local(2026, 10, 30, 18, 5)).hour is None


def test_today_falls_back_to_first_snapshot_today():
    snaps = [snap(local(2026, 10, 30, 10), 10), snap(local(2026, 10, 30, 11), 13)]
    assert compute(snaps, local(2026, 10, 30, 11, 5)).today.lo == 3


def test_single_snapshot_has_no_deltas():
    m = compute([snap(local(2026, 10, 30, 10), 10)], local(2026, 10, 30, 10, 5))
    assert m.hour is None and m.today is None and m.yesterday is None


def _metrics(lo):
    snaps = [snap(local(2026, 10, 30, 10), 100), snap(local(2026, 10, 30, 11), 100 + lo)]
    return compute(snaps, local(2026, 10, 30, 11, 5))


def test_rule_threshold():
    assert [r.name for r in triggered(DEFAULT_RULES, _metrics(5), 9.9)] == ["近1小时新增>=5"]
    assert triggered(DEFAULT_RULES, _metrics(4), 9.9) == []


def test_price_rule_fires_on_two_for_expensive_items():
    names = [r.name for r in triggered(DEFAULT_RULES, _metrics(2), 59)]
    assert names == ["价格>=29且近1小时新增>1"]
    assert triggered(DEFAULT_RULES, _metrics(1), 59) == []
    assert triggered(DEFAULT_RULES, _metrics(2), None) == []


def test_rounded_lower_bound_used_for_alerts():
    snaps = [snap(local(2026, 10, 30, 10), 9990), snap(local(2026, 10, 30, 11), 10000, 1000)]
    m = compute(snaps, local(2026, 10, 30, 11, 5))
    assert [r.name for r in triggered(DEFAULT_RULES, m, 9.9)] == ["近1小时新增>=5"]
