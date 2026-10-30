import json
import re
from dataclasses import dataclass

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
DETAIL_URL = "https://www.xiaohongshu.com/goods-detail/{}"
API_MARK = "/detail/h5/toc"
RISK_STATUS = {403, 429, 461, 471}
_PRICE = re.compile(r"¥\s*([\d.]+)")
_RISK_TEXT = re.compile(r"验证|captcha|滑块|访问异常", re.I)


class FetchError(Exception):
    pass


class VerificationRequired(Exception):
    """遇到验证或限流，调用方应暂停整轮抓取，不要重试。"""


@dataclass(frozen=True)
class Fetched:
    sales_text: str | None
    shop_sales: str | None
    title: str | None
    price: float | None
    shop_id: str | None = None
    shop_name: str | None = None
    fans: str | None = None


def _walk(o):
    if isinstance(o, str) and o[:1] in "[{":
        try:
            o = json.loads(o)
        except ValueError:
            return
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def extract_fields(payload: dict) -> dict:
    found: dict = {}
    wanted = {"itemAnalysisDataText": "sales_text", "salesVolume": "shop_sales"}
    for k, v in _walk(payload):
        if k in wanted and isinstance(v, str):
            found.setdefault(wanted[k], v)
        elif k == "descriptionH5" and isinstance(v, dict) and isinstance(v.get("name"), str):
            found.setdefault("title", v["name"])
        elif k == "sellerH5" and isinstance(v, dict):
            for src, dst in (("id", "shop_id"), ("name", "shop_name"), ("fansAmount", "fans")):
                if isinstance(v.get(src), str):
                    found.setdefault(dst, v[src])
    return found


def extract_price(page_text: str) -> float | None:
    m = _PRICE.search(page_text)
    return float(m[1]) if m else None


class Fetcher:
    """复用一个无登录的浏览器上下文，逐个加载商品页，由页面脚本自己完成请求签名。"""

    def __init__(self, headless: bool = True, channel: str = "chrome"):
        self._headless = headless
        self._channel = channel
        self._pw = self._browser = self._ctx = self._page = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless, channel=self._channel)
        self._ctx = await self._browser.new_context(user_agent=MOBILE_UA)
        self._page = await self._ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        if self._ctx:
            await self._ctx.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch(self, item_id: str) -> Fetched:
        page = self._page
        try:
            async with page.expect_response(lambda r: API_MARK in r.url, timeout=15000) as info:
                await page.goto(DETAIL_URL.format(item_id), wait_until="domcontentloaded", timeout=20000)
            resp = await info.value
        except Exception as e:
            raise FetchError(f"加载失败: {type(e).__name__}") from e
        if resp.status in RISK_STATUS:
            raise VerificationRequired(f"接口返回 {resp.status}")
        if resp.status != 200:
            raise FetchError(f"接口返回 {resp.status}")
        try:
            fields = extract_fields(await resp.json())
        except Exception as e:
            raise FetchError("响应不是有效 JSON") from e
        try:
            await page.wait_for_selector("text=/已售/", timeout=5000)
        except Exception:
            pass
        text = await page.inner_text("body")
        if "sales_text" not in fields and _RISK_TEXT.search(text):
            raise VerificationRequired("页面出现验证提示")
        return Fetched(fields.get("sales_text"), fields.get("shop_sales"),
                       fields.get("title"), extract_price(text),
                       fields.get("shop_id"), fields.get("shop_name"), fields.get("fans"))
