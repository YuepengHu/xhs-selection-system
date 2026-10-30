import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from collections.abc import Callable

import httpx

from .settings import Settings

log = logging.getLogger(__name__)


class Notifier:
    """推送渠道从设置里实时读取；都没配置时只写日志。send 返回各渠道是否成功。"""

    def __init__(self, client: httpx.AsyncClient | None = None,
                 settings: Callable[[], Settings] = Settings):
        self._client = client or httpx.AsyncClient(timeout=10)
        self._settings = settings

    async def send(self, title: str, body: str) -> dict[str, bool]:
        log.info("通知: %s\n%s", title, body)
        s, result = self._settings(), {}
        if s.pushplus_token:
            result["pushplus"] = await self._post("https://www.pushplus.plus/send", {
                "token": s.pushplus_token, "title": title, "content": body, "template": "markdown"},
                ok=lambda d: d.get("code") == 200)
        if s.dingtalk_webhook:
            result["dingtalk"] = await self._post(_ding_url(s), {
                "msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{body}"}},
                ok=lambda d: d.get("errcode") == 0)
        return result

    async def _post(self, url: str, payload: dict, ok: Callable[[dict], bool]) -> bool:
        # 两个服务失败时也返回 HTTP 200，要看返回内容里的状态码
        try:
            r = await self._client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("通知发送失败: %s", type(e).__name__)
            return False
        if not isinstance(data, dict) or not ok(data):
            reason = data.get("msg") or data.get("errmsg") if isinstance(data, dict) else ""
            log.warning("通知被服务拒绝: %s", str(reason)[:80])
            return False
        return True


def _ding_url(s: Settings) -> str:
    if not s.dingtalk_secret:
        return s.dingtalk_webhook
    ts = str(round(time.time() * 1000))
    digest = hmac.new(s.dingtalk_secret.encode(), f"{ts}\n{s.dingtalk_secret}".encode(),
                      hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    return f"{s.dingtalk_webhook}&timestamp={ts}&sign={sign}"
