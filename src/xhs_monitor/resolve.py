import re

import httpx

from .fetcher import MOBILE_UA
from .parse import extract_item_id

_SHORT = re.compile(r"(?:https?://)?xhslink\.com/[^\s,，;；]+")
_LONG = re.compile(r"https?://www\.xiaohongshu\.com/goods-detail/[^\s,，;；]+")
_BARE = re.compile(r"\b[0-9a-f]{24}\b")


async def resolve_items(text: str, client: httpx.AsyncClient) -> tuple[list[str], list[str]]:
    """从粘贴的文本中提取商品 ID，短链会跟随跳转；返回 (ids, 无法识别的片段)。"""
    ids: list[str] = []
    bad: list[str] = []

    def add(i: str | None, src: str):
        if i is None:
            bad.append(src)
        elif i not in ids:
            ids.append(i)

    for m in _LONG.finditer(text):
        add(extract_item_id(m[0]), m[0])
    for m in _SHORT.finditer(text):
        url = m[0] if m[0].startswith("http") else "https://" + m[0]
        try:
            r = await client.get(url, headers={"User-Agent": MOBILE_UA}, follow_redirects=True)
            add(extract_item_id(str(r.url)), m[0])
        except httpx.HTTPError:
            bad.append(m[0])
    for m in _BARE.finditer(_LONG.sub("", text)):
        add(m[0], m[0])
    return ids, bad
