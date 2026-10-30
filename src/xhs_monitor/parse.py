import re
from dataclasses import dataclass

_EXACT = re.compile(r"^已售\s*(\d+)$")
_WAN = re.compile(r"^已售\s*(\d+)(?:\.(\d+))?万\+?$")
_ITEM_ID = re.compile(r"goods-detail/([0-9a-f]{24})")
_BARE_ID = re.compile(r"^[0-9a-f]{24}$")


@dataclass(frozen=True)
class Sales:
    raw: str
    low: int
    step: int

    @property
    def exact(self) -> bool:
        return self.step == 1

    @property
    def high(self) -> int:
        return self.low + self.step - 1


def parse_sales(text: str) -> Sales | None:
    """已售N 为精确值；已售x.x万(+) 按小数位推断精度，真实值落在 [low, low+step-1]。"""
    t = text.strip()
    if m := _EXACT.match(t):
        return Sales(t, int(m[1]), 1)
    if m := _WAN.match(t):
        decimals = len(m[2] or "")
        low = round(float(f"{m[1]}.{m[2] or '0'}") * 10000)
        return Sales(t, low, 10 ** max(4 - decimals, 0))
    return None


def extract_item_id(text: str) -> str | None:
    t = text.strip()
    if _BARE_ID.match(t):
        return t
    if m := _ITEM_ID.search(t):
        return m[1]
    return None
