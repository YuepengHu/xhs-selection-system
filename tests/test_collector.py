from pathlib import Path

import pytest

from xhs_collector.adb import goods_id_from_dumpsys
from xhs_collector.collector import Abort, Collector, Config, DailyCap, Filters
from xhs_collector.ui import Node, is_chooser, parse_cards, parse_detail, parse_dump, risk_text

FIX = Path(__file__).parent / "fixtures"
H = 2772


def test_parse_real_goods_list_skips_partially_visible_cards():
    cards = parse_cards(parse_dump((FIX / "goods1.xml").read_text(encoding="utf-8")), H)
    assert [(c.price, c.sales_text, c.shop, c.shop_sales) for c in cards] == [
        (2.99, "已售5480", "优学资料屋的店", "销量3万+"),
        (2.23, "已售2418", "上岸吧你的店", "销量1万+"),
    ]
    assert cards[0].title.startswith("【秒发】教师资格证历年真题")


def test_parse_real_detail_and_chooser():
    d = parse_detail(parse_dump((FIX / "detail1.xml").read_text(encoding="utf-8")))
    assert (d.price, d.sales_text) == (2.99, "已售5480") and d.title.startswith("【秒发】")
    assert is_chooser(parse_dump((FIX / "chooser.xml").read_text(encoding="utf-8")))
    assert not is_chooser(parse_dump((FIX / "goods1.xml").read_text(encoding="utf-8")))


def test_risk_text_ignores_long_titles():
    long_title = Node("验证码识别教程合集，从入门到精通共五十集完整版资料打包下载", "", 0, 0, 1, 1)
    assert risk_text([long_title]) is None
    assert risk_text([Node("请完成安全验证", "", 0, 0, 1, 1)]) == "安全验证"


def test_goods_id_from_dumpsys_uses_top_record_only():
    dump = (
        "  topResumedActivity=ActivityRecord{173791103 u999 com.xingin.xhs/.GoodsDetailActivityV2 t31263}\n"
        "    * Hist  #2: ActivityRecord{173791103 u999 com.xingin.xhs/.GoodsDetailActivityV2 t31263}\n"
        "        packageName=com.xingin.xhs\n"
        "        Intent { dat=xhsdiscover://goods_detail/6a3ebd7c9f30700001e82240?listing_image=x cmp=a/b }\n"
        "    * Hist  #1: ActivityRecord{999 u999 com.xingin.xhs/.Search t31263}\n"
        "        Intent { dat=xhsdiscover://goods_detail/111111111111111111111111 }\n"
    )
    assert goods_id_from_dumpsys(dump, "173791103") == "6a3ebd7c9f30700001e82240"
    assert goods_id_from_dumpsys(dump, "42") is None


class FakeAdb:
    """模拟商品列表和详情页：双列、可滚动，点标题进详情，返回回到列表。"""

    ROW = 700

    def __init__(self, products, risk_on_detail=False):
        self.products, self.state, self.cur, self.scroll = products, "search", None, 0
        self.risk_on_detail = risk_on_detail
        self.taps = 0

    def screen_height(self):
        return H

    def focus(self):
        return ("com.xingin.xhs", "GlobalSearchActivity" if self.state == "search" else "GoodsDetailActivityV2")

    def _pos(self, i):
        return 630 * (i % 2) + 52, 600 + (i // 2) * self.ROW - self.scroll

    def dump(self):
        if self.state == "detail":
            p = self.cur
            nodes = [Node("", f"商品名称，{p['title']}", 0, 1414, 1260, 1593), Node(f"¥{p['price']}", "", 56, 1308, 225, 1408),
                     Node(p["sales"], "", 1032, 1326, 1204, 1389)]
            if self.risk_on_detail:
                nodes.append(Node("请完成安全验证", "", 0, 0, 10, 10))
            return nodes
        nodes = [Node("商品", "", 504, 371, 616, 435), Node("综合", "", 84, 529, 183, 584)]
        for i, p in enumerate(self.products):
            x, y = self._pos(i)
            if y < 560 or y + 400 > H:
                continue
            nodes += [Node(p["title"], "", x, y, x + 500, y + 70),
                      Node(f"¥{p['price']}{p['sales']}", "", x, y + 160, x + 300, y + 235),
                      Node(p["shop"], "", x + 80, y + 250, x + 400, y + 300)]
        return nodes

    def tap(self, x, y):
        self.taps += 1
        if self.state != "search":
            return
        for i, p in enumerate(self.products):
            px, py = self._pos(i)
            if px <= x <= px + 500 and py <= y <= py + 70 and py >= 600:
                self.state, self.cur = "detail", p

    def back(self):
        self.state = "search"

    def swipe_up(self, h):
        self.scroll = min(self.scroll + 1400, max(0, (len(self.products) // 2) * self.ROW - 1500))

    def start_search(self, kw):
        pass

    def top_goods_id(self):
        return self.cur["id"] if self.state == "detail" else None

    def screenshot(self, path):
        Path(path).write_bytes(b"png")


def products(n):
    return [{"id": f"{i:024x}", "title": f"教资真题资料合集第{i}号", "price": 2.99 + i, "sales": f"已售{100 * i}",
             "shop": f"店铺{i}的店"} for i in range(1, n + 1)]


def make(tmp_path, fake, **cfg):
    c = Config(delay=(0, 0), wait_timeout=1.0, out_dir=tmp_path, **cfg)
    return Collector(fake, c, sleep=lambda a, b: None), DailyCap(tmp_path / "state.json", c.daily_cap)


def test_collects_across_scrolls_without_duplicates(tmp_path):
    col, cap = make(tmp_path, FakeAdb(products(10)), limit=8)
    recs = col.run("教资", Filters(), cap)
    ids = [r.item_id for r in recs]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert ids == [f"{i:024x}" for i in range(1, 9)]
    assert cap.used == 8


def test_stops_at_end_of_list(tmp_path):
    col, cap = make(tmp_path, FakeAdb(products(5)), limit=50)
    assert len(col.run("教资", Filters(), cap)) == 5


def test_filters_applied_before_opening_detail(tmp_path):
    fake = FakeAdb(products(6))
    col, cap = make(tmp_path, fake, limit=50)
    recs = col.run("教资", Filters(min_sales=400), cap)
    assert [r.sales_text for r in recs] == ["已售400", "已售500", "已售600"]
    assert cap.used == 3


def test_daily_cap_stops_collection(tmp_path):
    col, cap = make(tmp_path, FakeAdb(products(10)), limit=50, daily_cap=3)
    assert len(col.run("教资", Filters(), cap)) == 3


def test_risk_prompt_aborts_with_screenshot(tmp_path):
    col, cap = make(tmp_path, FakeAdb(products(4), risk_on_detail=True), limit=5)
    with pytest.raises(Abort) as e:
        col.run("教资", Filters(), cap)
    assert "安全验证" in str(e.value) and list(tmp_path.glob("abort-*.png"))


def test_daily_cap_persists(tmp_path):
    cap = DailyCap(tmp_path / "state.json", 2)
    cap.add()
    cap.add()
    assert DailyCap(tmp_path / "state.json", 2).left == 0
