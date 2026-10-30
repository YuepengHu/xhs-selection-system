from xhs_monitor.parse import extract_item_id, parse_sales


def test_exact_values():
    for text, n in [("已售5471", 5471), ("已售6514", 6514), ("已售7832", 7832), ("已售9999", 9999)]:
        s = parse_sales(text)
        assert s.exact and s.low == n and s.high == n


def test_wan_with_decimal_step_is_1000():
    s = parse_sales("已售1.8万+")
    assert (s.low, s.step, s.exact) == (18000, 1000, False)
    assert s.high == 18999


def test_wan_without_decimal_step_is_10000():
    s = parse_sales("已售12万+")
    assert (s.low, s.step) == (120000, 10000)


def test_shop_style_with_space_and_no_plus():
    assert parse_sales("已售 3.8万").low == 38000
    assert parse_sales("已售 14.2万").low == 142000


def test_unparseable_returns_none():
    for text in ["", "销量1000", "已售", "已售abc万"]:
        assert parse_sales(text) is None


def test_extract_item_id():
    i = "67ed05f404afe300019465be"
    assert extract_item_id(i) == i
    assert extract_item_id(f"https://www.xiaohongshu.com/goods-detail/{i}?xsec_token=x&appuid=1") == i
    assert extract_item_id("https://www.xiaohongshu.com/explore/abc") is None
