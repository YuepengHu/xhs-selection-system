import asyncio
import base64
import hashlib
import hmac

import httpx

from xhs_monitor.notify import Notifier
from xhs_monitor.settings import Settings


def _send(settings: Settings, handler) -> dict:
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await Notifier(c, lambda: settings).send("标题", "内容")
    return asyncio.run(go())


def test_pushplus_success_and_rejected_token_with_http_200():
    seen = {}

    def ok(req):
        seen["body"] = req.read()
        return httpx.Response(200, json={"code": 200, "msg": "请求成功", "data": "abc"})

    assert _send(Settings(pushplus_token="tk"), ok) == {"pushplus": True}
    assert b'"token":"tk"' in seen["body"].replace(b" ", b"")

    bad = lambda req: httpx.Response(200, json={"code": 903, "msg": "用户令牌不正确", "data": "无效的用户token"})
    assert _send(Settings(pushplus_token="tk"), bad) == {"pushplus": False}


def test_dingtalk_errcode_and_signature():
    seen = {}

    def ok(req):
        seen["url"] = req.url
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    s = Settings(dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=a", dingtalk_secret="SEC")
    assert _send(s, ok) == {"dingtalk": True}
    q = seen["url"].params
    ts, sign = q["timestamp"], q["sign"]
    want = base64.b64encode(hmac.new(b"SEC", f"{ts}\nSEC".encode(), hashlib.sha256).digest()).decode()
    assert sign == want and q["access_token"] == "a"

    bad = lambda req: httpx.Response(200, json={"errcode": 310000, "errmsg": "sign not match"})
    assert _send(s, bad) == {"dingtalk": False}


def test_http_error_and_non_json_count_as_failure():
    s = Settings(pushplus_token="tk")
    assert _send(s, lambda r: httpx.Response(500)) == {"pushplus": False}
    assert _send(s, lambda r: httpx.Response(200, text="<html>")) == {"pushplus": False}


def test_nothing_configured_sends_nothing():
    assert _send(Settings(), lambda r: httpx.Response(500)) == {}
