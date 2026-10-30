import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from xhs_monitor.parse import parse_sales

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_PRICE = r"(?:券后|到手价)?¥(\d+(?:\.\d*)?)"
_PRICE_SALES = re.compile(rf"^{_PRICE}(已售\d+(?:\.\d+)?万?\+?)$")
_PRICE_ONLY = re.compile(rf"^{_PRICE}$")
_DETAIL_SALES = re.compile(r"^已售\d+(?:\.\d+)?万?\+?$")
_SHOP_SALES = re.compile(r"^销量")
_NOT_TITLE = re.compile(r"加购|人购买|好评|今日发|^满\d+减|^“|^¥|^券后|^到手价|^已售|^销量|当月|第\d+名|上新\d+件|年老店|^立减")
_BADGE = re.compile(r"加购|人购买|好评率|立减|^满\d+减|今日发|当月|第\d+名")
_DATE = re.compile(r"^(今天|昨天|\d{2}-\d{2})$")
_RISK = re.compile(r"安全验证|滑块|拖动|验证码|访问异常|操作频繁|请稍后再试|账号异常")
_CHOOSER = "选择要使用的应用"


@dataclass(frozen=True)
class Node:
    text: str
    desc: str
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def label(self) -> str:
        return self.text or self.desc

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2


@dataclass(frozen=True)
class Card:
    title: str
    price: float
    sales_text: str | None
    shop: str | None
    shop_sales: str | None
    tap: tuple[int, int]
    shop_tap: tuple[int, int] | None = None
    cart_info: str = ""
    shop_tags: str = ""
    top: int = 0

    @property
    def key(self) -> str:
        return f"{self.title}|{self.price}|{self.shop}"


def parse_dump(xml: str) -> list[Node]:
    nodes = []
    for el in ET.fromstring(xml).iter("node"):
        m = _BOUNDS.match(el.get("bounds", ""))
        text, desc = el.get("text", ""), el.get("content-desc", "")
        if m and (text or desc):
            nodes.append(Node(text, desc, *map(int, m.groups())))
    return nodes


def find(nodes: list[Node], text: str, *, y_min: int = 0, y_max: int = 10**6) -> Node | None:
    for n in nodes:
        if n.label == text and y_min <= n.cy <= y_max:
            return n
    return None


def is_chooser(nodes: list[Node]) -> bool:
    return any(n.label == _CHOOSER for n in nodes)


def risk_text(nodes: list[Node]) -> str | None:
    for n in nodes:
        if len(n.label) <= 24 and (m := _RISK.search(n.label)):
            return m[0]
    return None


def parse_cards(nodes: list[Node], screen_h: int, top: int = 600, bottom_margin: int = 150,
                shop_row: bool = True) -> list[Card]:
    """商品列表为双列。以"价格(+已售)"节点为锚点，向上找标题，向下找店铺。
    上新列表的商品没有"已售"，sales_text 为 None。"""
    seen_anchor = set()
    cards = []
    for p in nodes:
        m = _PRICE_SALES.match(p.label) or _PRICE_ONLY.match(p.label)
        if not m or (p.x1, p.y1) in seen_anchor:
            continue
        seen_anchor.add((p.x1, p.y1))
        sales_text = m[2] if m.re is _PRICE_SALES else None
        col_x1, col_x2 = (0, 630) if p.x1 < 630 else (630, 1300)
        in_col = [n for n in nodes if col_x1 <= n.x1 < col_x2 and abs(n.x1 - p.x1) <= 90]
        titles = [n for n in in_col if abs(n.x1 - p.x1) <= 30 and n.y2 <= p.y1 + 5 and n.y1 >= p.y1 - 320
                  and len(n.label) >= 6 and not _NOT_TITLE.search(n.label)]
        if not titles:
            continue
        title = max(titles, key=lambda n: len(n.label))
        if title.y1 < top or p.y2 > screen_h - bottom_margin:
            continue
        badges = [n.label for n in in_col if title.y2 - 5 <= n.y1 <= p.y1 and _BADGE.search(n.label)]
        shop = shop_sales = shop_node = None
        tags: list[str] = []
        if shop_row:
            below = sorted((n for n in nodes if col_x1 <= n.x1 < col_x2 and p.y2 - 5 <= n.y1 <= p.y2 + 200),
                           key=lambda n: n.x1)
            shop_sales = next((n.label for n in below if _SHOP_SALES.match(n.label)), None)
            named = [n for n in below if not _SHOP_SALES.match(n.label) and len(n.label) >= 2]
            if named:
                shop_node = named[0]
                shop = shop_node.label
                tags = [n.label for n in named[1:]]
        cards.append(Card(title.label, float(m[1].rstrip(".") or 0), sales_text, shop, shop_sales,
                          (title.cx, title.cy), (shop_node.cx, shop_node.cy) if shop_node else None,
                          " ".join(badges), " ".join(tags), title.y1))
    return sorted(cards, key=lambda c: (c.tap[1], c.tap[0]))


def date_headers(nodes: list[Node], top: int = 600) -> list[tuple[int, str]]:
    """上新列表里每组商品上方的日期标题，按 y 从上到下。"""
    return sorted((n.y1, n.label) for n in nodes if _DATE.match(n.label) and n.x1 < 200 and n.y1 > top)


@dataclass(frozen=True)
class Detail:
    title: str | None
    price: float | None
    sales_text: str | None


def parse_detail(nodes: list[Node]) -> Detail:
    title = next((n.desc.split("，", 1)[1] for n in nodes if n.desc.startswith("商品名称，")), None)
    price = next((float(n.text[1:]) for n in nodes if re.fullmatch(r"¥\d+(?:\.\d+)?", n.text)), None)
    sales = next((n.label for n in nodes if _DETAIL_SALES.match(n.label)), None)
    return Detail(title, price, sales)


def is_detail_loaded(nodes: list[Node]) -> bool:
    return any(n.label.startswith("已售") or n.desc.startswith("商品名称") for n in nodes)


def sales_low(sales_text: str | None) -> int | None:
    if sales_text is None:
        return 0
    s = parse_sales(sales_text)
    return s.low if s else None
