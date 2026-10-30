import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from xhs_monitor.fetcher import FetchError, Fetched, VerificationRequired, extract_fields, extract_price
from xhs_monitor.runner import is_due, run_once
from xhs_monitor.store import Store

ID = "67ed05f404afe300019465be"
T0 = datetime(2026, 10, 30, 3, 1, tzinfo=timezone.utc)


class FakeFetcher:
    def __init__(self, results):
        self.results = list(results)

    async def fetch(self, item_id):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, title, body):
        self.sent.append((title, body))


async def nosleep(_):
    return None


def fetched(sales, price=9.9):
    return Fetched(sales, "已售 3.8万", "测试商品", price)


def run(store, results, now, notifier=None):
    notifier = notifier or FakeNotifier()
    r = asyncio.run(run_once(store, FakeFetcher(results), notifier, now=now, sleep=nosleep))
    return r, notifier


def test_extract_fields_handles_json_in_string_and_nested():
    payload = {"data": {"template_data": '[{"priceH5": {"itemAnalysisDataText": "已售5471"},'
                        '"sellerH5": {"salesVolume": "已售 3.8万"}, "descriptionH5": {"name": "真题"}}]'}}
    assert extract_fields(payload) == {"sales_text": "已售5471", "shop_sales": "已售 3.8万", "title": "真题"}


def test_extract_price():
    assert extract_price("1/3\n¥\n\n2.99\n\n已售5471") == 2.99
    assert extract_price("无价格") is None


def test_alert_sent_once_then_cooldown():
    s = Store()
    s.add_item(ID, "未分类", T0)
    run(s, [fetched("已售100")], T0)
    r, n = run(s, [fetched("已售106")], T0 + timedelta(hours=1))
    assert r.alerts == 1 and len(n.sent) == 1 and "近1小时新增>=5" in n.sent[0][1]
    r, n = run(s, [fetched("已售113")], T0 + timedelta(hours=2))
    assert r.alerts == 0 and n.sent == []


def test_verification_pauses_and_skips_next_run():
    s = Store()
    s.add_item(ID, "未分类", T0)
    r, n = run(s, [VerificationRequired("接口返回 461")], T0)
    assert r.paused and n.sent[0][0] == "小红书监控已暂停"
    r2, _ = run(s, [], T0 + timedelta(hours=1))
    assert r2.skipped and r2.fetched == 0
    s.resume()
    r3, _ = run(s, [fetched("已售1")], T0 + timedelta(hours=1))
    assert r3.fetched == 1


def test_fetch_error_and_parse_failure_recorded_not_counted_as_snapshot():
    s = Store()
    s.add_item(ID, "未分类", T0)
    r, _ = run(s, [FetchError("加载失败"), fetched("销量暂无")], T0)
    assert r.failed == 1
    r, _ = run(s, [fetched("销量暂无")], T0 + timedelta(hours=1))
    assert r.failed == 1 and s.snapshots(ID) == []
    assert [h["error"] for h in s.history(ID)] == ["销量无法解析", "加载失败"]


def test_rounded_items_refresh_daily_exact_items_hourly():
    s = Store()
    s.add_item(ID, "未分类", T0)
    assert is_due(s, ID, T0)
    run(s, [fetched("已售1.8万+")], T0)
    assert not is_due(s, ID, T0 + timedelta(hours=1))
    assert not is_due(s, ID, T0 + timedelta(hours=21))
    assert is_due(s, ID, T0 + timedelta(hours=23))
    s2 = Store()
    s2.add_item(ID, "未分类", T0)
    run(s2, [fetched("已售100")], T0)
    assert is_due(s2, ID, T0 + timedelta(hours=1))


def test_hour_slot_due_logic_and_catch_up():
    s = Store()
    s.add_item(ID, "未分类", T0)
    run(s, [fetched("已售100")], T0)
    assert not is_due(s, ID, T0 + timedelta(minutes=30))
    assert is_due(s, ID, T0 + timedelta(hours=1))
    assert is_due(s, ID, T0 + timedelta(hours=5))


def test_not_due_outside_active_window_but_first_snapshot_always_due():
    s = Store()
    s.add_item(ID, "未分类", T0)
    night = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    assert is_due(s, ID, night)
    run(s, [fetched("已售100")], T0)
    assert not is_due(s, ID, night)
    assert is_due(s, ID, night, window=(0, 23))


def test_failed_attempts_limited_per_hour_with_retry_gap():
    s = Store()
    s.add_item(ID, "未分类", T0)
    run(s, [fetched("已售100")], T0)
    t = T0 + timedelta(hours=1)
    run(s, [FetchError("x")], t)
    assert not is_due(s, ID, t + timedelta(minutes=5))
    assert is_due(s, ID, t + timedelta(minutes=11))
    run(s, [FetchError("x")], t + timedelta(minutes=11))
    assert not is_due(s, ID, t + timedelta(minutes=25))


def test_store_delete_cascades_and_category():
    s = Store()
    s.add_item(ID, "未分类", T0)
    assert not s.add_item(ID, "x", T0)
    s.set_category(ID, "教资")
    run(s, [fetched("已售100")], T0)
    assert s.get_item(ID).category == "教资" and s.get_item(ID).title == "测试商品"
    assert s.delete_item(ID) and s.history(ID) == []
