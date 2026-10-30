import csv
import json
import logging
import time
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path

from .adb import PKG, sleep_jitter
from .ui import (Card, date_headers, find, is_chooser, is_detail_loaded, parse_cards, parse_detail,
                 risk_text, sales_low)

log = logging.getLogger(__name__)
SEARCH_ACT = "GlobalSearchActivity"
DETAIL_ACT = "GoodsDetailActivity"
SHOP_ACT = "ShopDetailActivity"
CHOOSER_PKG = "com.vivo.doubleinstance"
MAX_SEEN = 20000


class Abort(Exception):
    """遇到无法继续的状态（验证、被带离 App、页面结构变化），整次采集停止。"""


class Stop(Exception):
    """用户通过 STOP 文件或 Ctrl+C 要求停止，已采集的结果照常保存。"""


@dataclass
class Filters:
    min_sales: int = 0
    min_price: float | None = None
    max_price: float | None = None
    only_with_sales: bool = False
    new_days: int | None = None

    def passes(self, c: Card) -> bool:
        low = sales_low(c.sales_text)
        if low is None or low < self.min_sales:
            return False
        if self.only_with_sales and low < 1:
            return False
        if self.min_price is not None and c.price < self.min_price:
            return False
        if self.max_price is not None and c.price > self.max_price:
            return False
        return True


def days_ago(label: str | None, today: date | None = None) -> int | None:
    """上新分组标题（今天、昨天、MM-DD）距今几天；无法判断返回 None。"""
    today = today or date.today()
    if label == "今天":
        return 0
    if label == "昨天":
        return 1
    try:
        m, d = map(int, (label or "").split("-"))
        dt = date(today.year, m, d)
        if dt > today:
            dt = date(today.year - 1, m, d)
        return (today - dt).days
    except ValueError:
        return None


@dataclass
class Config:
    instance: str = "Ⅱ·小红书"
    limit: int = 20
    daily_cap: int = 100
    sort_sales: bool = False
    delay: tuple[float, float] = (2.0, 4.0)
    max_idle_swipes: int = 3
    wait_timeout: float = 10.0
    out_dir: Path = Path("data/collector")


@dataclass
class Record:
    source: str
    item_id: str
    title: str
    price: float | None
    sales_text: str | None
    shop: str | None
    shop_sales: str | None
    shop_tags: str
    cart_info: str
    listed_date: str | None
    shop_id: str | None
    collected_at: str
    remark: str = ""


HEADERS = {"source": "来源", "item_id": "商品ID", "title": "商品标题", "price": "价格", "sales_text": "销量",
           "shop": "店铺名称", "shop_sales": "店铺销量", "shop_tags": "店铺标签", "cart_info": "加购信息",
           "listed_date": "上新日期", "shop_id": "店铺ID", "collected_at": "采集时间", "remark": "备注"}


class DailyCap:
    """每日点开次数，以及已看过的商品卡片（用于重复运行时跳过，相当于断点续跑）。"""

    def __init__(self, path: Path, cap: int):
        self._path, self._cap = path, cap
        self._today = date.today().isoformat()
        data = json.loads(path.read_text()) if path.exists() else {}
        counts = data.get("counts", {k: v for k, v in data.items() if k != "seen"})
        self._count = counts.get(self._today, 0)
        self._seen: list[str] = list(data.get("seen", []))
        self._seen_set = set(self._seen)

    @property
    def used(self) -> int:
        return self._count

    @property
    def left(self) -> int:
        return max(self._cap - self._count, 0)

    def is_seen(self, key: str) -> bool:
        return key in self._seen_set

    def add(self, seen_key: str | None = None) -> None:
        self._count += 1
        if seen_key and seen_key not in self._seen_set:
            self._seen.append(seen_key)
            self._seen_set.add(seen_key)
            if len(self._seen) > MAX_SEEN:
                self._seen_set.discard(self._seen.pop(0))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"counts": {self._today: self._count}, "seen": self._seen},
                                         ensure_ascii=False))


class Control:
    """out_dir 下放 STOP 文件停止，放 PAUSE 文件暂停，删除后继续。"""

    def __init__(self, out_dir: Path, sleep=time.sleep):
        self._stop, self._pause, self._sleep = out_dir / "STOP", out_dir / "PAUSE", sleep

    def check(self) -> None:
        while self._pause.exists() and not self._stop.exists():
            self._sleep(1)
        if self._stop.exists():
            raise Stop("检测到 STOP 文件")


class Collector:
    def __init__(self, adb, cfg: Config, sleep=sleep_jitter, control: Control | None = None):
        self.adb, self.cfg, self._sleep = adb, cfg, sleep
        self.control = control or Control(cfg.out_dir)
        self.screen_h = adb.screen_height()

    # ---- 等待与保护 ----
    def _wait(self, pred, what: str):
        end = time.monotonic() + self.cfg.wait_timeout
        while time.monotonic() < end:
            nodes = self.adb.dump()
            pkg, act = self.adb.focus()
            if (reason := risk_text(nodes)):
                self._fail(f"出现验证或风控提示：{reason}")
            if pkg == CHOOSER_PKG or is_chooser(nodes):
                self._pick_instance(nodes)
                continue
            if pred(nodes, pkg, act):
                return nodes
            time.sleep(0.8)
        self._fail(f"等待超时：{what}（当前 {self.adb.focus()}）")

    def _fail(self, msg: str):
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        shot = self.cfg.out_dir / f"abort-{datetime.now():%Y%m%d-%H%M%S}.png"
        try:
            pkg, _ = self.adb.focus()
            if pkg in ("com.android.systemui", "com.vivo.keyguard", "com.android.keyguard"):
                msg += "（手机处于锁屏状态，请解锁；可用 awake on 或默认的常亮功能避免熄屏）"
        except Exception:
            pass
        try:
            self.adb.screenshot(str(shot))
            msg += f"，已截图 {shot}"
        except Exception:
            pass
        raise Abort(msg)

    def _pick_instance(self, nodes) -> None:
        n = find(nodes, self.cfg.instance)
        if n is None:
            self._fail(f"应用选择框里没有“{self.cfg.instance}”")
        self.adb.tap(n.cx, n.y1 - 60)

    def _has_cards(self, ns, act_prefix, pkg, act, shop_row=True) -> bool:
        return pkg == PKG and act.startswith(act_prefix) and bool(parse_cards(ns, self.screen_h, shop_row=shop_row))

    # ---- 入口 ----
    def open_goods_tab(self, keyword: str) -> None:
        self.adb.start_search(keyword)
        nodes = self._wait(lambda ns, pkg, act: pkg == PKG and act.startswith(SEARCH_ACT), "搜索结果页")
        tab = find(nodes, "商品", y_min=300, y_max=500)
        if tab is None:
            self._fail("搜索结果页没有“商品”标签")
        self.adb.tap(tab.cx, tab.cy)
        nodes = self._wait(lambda ns, pkg, act: find(ns, "综合", y_min=450, y_max=700) is not None
                           and self._has_cards(ns, SEARCH_ACT, pkg, act), "商品列表")
        if self.cfg.sort_sales:
            s = find(nodes, "销量", y_min=450, y_max=700)
            if s:
                self.adb.tap(s.cx, s.cy)
                self._wait(lambda ns, pkg, act: self._has_cards(ns, SEARCH_ACT, pkg, act), "按销量排序后的列表")

    def open_shop(self, seller_id: str, new_tab: bool = False, sort: str | None = None) -> None:
        self.adb.start_shop(seller_id)
        nodes = self._wait(lambda ns, pkg, act: pkg == PKG and act.startswith(SHOP_ACT)
                           and find(ns, "商品", y_min=350, y_max=500) is not None, "店铺页")
        self._shop_tab(nodes, new_tab, sort)

    def _shop_tab(self, nodes, new_tab: bool, sort: str | None) -> None:
        if new_tab:
            t = find(nodes, "上新", y_min=350, y_max=500)
            if t is None:
                self._fail("店铺页没有“上新”标签")
            self.adb.tap(t.cx, t.cy)
        elif sort:
            s = find(nodes, sort, y_min=480, y_max=640)
            if s is None:
                self._fail(f"店铺页没有“{sort}”排序")
            self.adb.tap(s.cx, s.cy)
        self._wait(lambda ns, pkg, act: self._has_cards(ns, SHOP_ACT, pkg, act, shop_row=False), "店铺商品列表")

    def find_shop_by_name(self, name: str) -> str:
        """在商品搜索结果里找店铺名匹配的卡片，点进店铺页，返回店铺 ID。"""
        self.open_goods_tab(name)
        prev: frozenset[str] = frozenset()
        for _ in range(self.cfg.max_idle_swipes + 3):
            nodes = self._wait(lambda ns, pkg, act: self._has_cards(ns, SEARCH_ACT, pkg, act), "商品列表")
            cards = parse_cards(nodes, self.screen_h)
            hit = next((c for c in cards if c.shop and c.shop_tap and (c.shop == name or name in c.shop)), None)
            if hit:
                self.adb.tap(*hit.shop_tap)
                self._wait(lambda ns, pkg, act: pkg == PKG and act.startswith(SHOP_ACT), "店铺页")
                seller_id = self.adb.top_shop_id()
                if not seller_id:
                    self._fail("读不到店铺 ID")
                return seller_id
            keys = frozenset(c.key for c in cards)
            if keys == prev:
                break
            prev = keys
            self.adb.swipe_up(self.screen_h)
            self._sleep(1.5, 2.5)
        self._fail(f"搜索结果里没有找到店铺“{name}”")

    # ---- 采集 ----
    def visit(self, card: Card, source: str, list_act: str, shop_id: str | None, listed: str | None) -> Record | None:
        self.adb.tap(*card.tap)
        nodes = self._wait(lambda ns, pkg, act: pkg == PKG and act.startswith(DETAIL_ACT)
                           and is_detail_loaded(ns), "商品详情页")
        item_id = self.adb.top_goods_id()
        detail = parse_detail(nodes)
        self.adb.back()
        self._wait(lambda ns, pkg, act: self._has_cards(ns, list_act, pkg, act, shop_row=list_act == SEARCH_ACT),
                   "返回商品列表")
        if item_id is None:
            log.warning("读不到商品 ID，跳过：%s", card.title[:20])
            return None
        return Record(source, item_id, detail.title or card.title, detail.price or card.price,
                      detail.sales_text or card.sales_text, card.shop, card.shop_sales, card.shop_tags,
                      card.cart_info, listed, shop_id, datetime.now().isoformat(timespec="seconds"))

    def _collect(self, source: str, filters: Filters, cap: DailyCap, on_record, *, list_act: str,
                 shop_id: str | None = None, new_mode: bool = False, limit: int | None = None) -> list[Record]:
        limit = self.cfg.limit if limit is None else limit
        shop_row = list_act == SEARCH_ACT
        seen: set[str] = set()
        records: list[Record] = []
        idle, prev_keys, carry = 0, frozenset(), "今天"
        while len(records) < limit:
            self.control.check()
            if cap.left <= 0:
                log.info("已达每日上限 %s，停止", cap.used)
                break
            nodes = self._wait(lambda ns, pkg, act: self._has_cards(ns, list_act, pkg, act, shop_row=shop_row),
                               "商品列表")
            cards = parse_cards(nodes, self.screen_h, shop_row=shop_row)
            labels = self._date_labels(cards, nodes, carry) if new_mode else {}
            for c in cards:
                skip = c.key in seen or cap.is_seen(c.key)
                too_old = new_mode and filters.new_days is not None and (
                    (d := days_ago(labels.get(c.key))) is not None and d > filters.new_days)
                if not skip and (too_old or not filters.passes(c)):
                    seen.add(c.key)
            todo = [c for c in cards if c.key not in seen and not cap.is_seen(c.key)]
            if todo:
                card = todo[0]
                seen.add(card.key)
                rec = self.visit(card, source, list_act, shop_id, labels.get(card.key))
                cap.add(card.key)
                if rec:
                    records.append(rec)
                    if on_record:
                        on_record(rec)
                self._sleep(*self.cfg.delay)
                continue
            if labels and cards:
                carry = labels[cards[-1].key]
            keys = frozenset(c.key for c in cards)
            idle = idle + 1 if keys == prev_keys else 0
            if idle >= self.cfg.max_idle_swipes:
                log.info("列表已到底")
                break
            prev_keys = keys
            self.adb.swipe_up(self.screen_h)
            self._sleep(1.5, 2.5)
        return records

    @staticmethod
    def _date_labels(cards: list[Card], nodes, carry: str) -> dict[str, str]:
        """每张卡片归属到它上方最近的日期标题；屏幕顶部没有标题时沿用上一屏的。"""
        heads = date_headers(nodes)
        out, cur = {}, carry
        for c in cards:
            above = [lab for y, lab in heads if y < c.top]
            cur = above[-1] if above else cur
            out[c.key] = cur
        return out

    # ---- 三种模式 ----
    def run(self, keyword: str, filters: Filters, cap: DailyCap, on_record=None, limit: int | None = None):
        self.open_goods_tab(keyword)
        return self._collect(f"关键词:{keyword}", filters, cap, on_record, list_act=SEARCH_ACT, limit=limit)

    def run_shop(self, seller_id: str, filters: Filters, cap: DailyCap, on_record=None, *,
                 new_tab: bool = False, sort: str | None = None, limit: int | None = None):
        self.open_shop(seller_id, new_tab, sort)
        return self._collect(f"店铺:{seller_id}", filters, cap, on_record, list_act=SHOP_ACT,
                             shop_id=seller_id, new_mode=new_tab, limit=limit)

    def run_shop_name(self, name: str, filters: Filters, cap: DailyCap, on_record=None, *,
                      new_tab: bool = False, sort: str | None = None, limit: int | None = None):
        seller_id = self.find_shop_by_name(name)
        nodes = self.adb.dump()
        self._shop_tab(nodes, new_tab, sort)
        return self._collect(f"店铺名:{name}", filters, cap, on_record, list_act=SHOP_ACT,
                             shop_id=seller_id, new_mode=new_tab, limit=limit)


def _rows(records: list[Record]):
    return [[asdict(r)[f.name] for f in fields(Record)] for r in records]


def write_csv(path: Path, records: list[Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADERS[x.name] for x in fields(Record))
        w.writerows(_rows(records))


def write_xlsx(path: Path, records: list[Record]) -> None:
    from openpyxl import Workbook
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append([HEADERS[x.name] for x in fields(Record)])
    for row in _rows(records):
        ws.append(row)
    for col, width in zip("ABCDEFGHIJKLM", (20, 26, 40, 8, 12, 18, 12, 16, 22, 10, 26, 20, 12)):
        ws.column_dimensions[col].width = width
    wb.save(path)
