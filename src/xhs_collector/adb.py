import random
import re
import subprocess
import time
import urllib.parse

from .ui import Node, parse_dump

PKG = "com.xingin.xhs"
_FOCUS = re.compile(r"mCurrentFocus=Window\{\w+ u(\d+) ([^/ ]+)/?([^ }]*)")


class AdbError(Exception):
    pass


class Adb:
    def __init__(self, serial: str | None = None, timeout: int = 30):
        self._timeout = timeout
        self.serial: str | None = None
        self.serial = serial or self._single_device()

    def _run(self, *args: str, binary: bool = False):
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        try:
            text_kw = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
            r = subprocess.run(cmd, capture_output=True, timeout=self._timeout, **text_kw)
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb 超时: {args[:2]}") from e
        if r.returncode != 0:
            raise AdbError(f"adb 失败: {args[:2]} {(r.stderr or '')[:120]}")
        return r.stdout

    def _single_device(self) -> str:
        out = self._run("devices")
        devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        if len(devs) != 1:
            raise AdbError(f"需要恰好一台设备，当前 {len(devs)} 台，请用 --serial 指定")
        return devs[0]

    def shell(self, cmd: str) -> str:
        return self._run("shell", cmd)

    def screen_height(self) -> int:
        m = re.search(r"Physical size: (\d+)x(\d+)", self.shell("wm size"))
        if not m:
            raise AdbError("无法读取屏幕尺寸")
        return int(m[2])

    def focus(self) -> tuple[str, str]:
        """返回 (包名, Activity 简名)，拿不到时返回空串。"""
        m = _FOCUS.search(self.shell("dumpsys window | grep mCurrentFocus"))
        return (m[2], m[3].rsplit(".", 1)[-1]) if m else ("", "")

    def dump(self) -> list[Node]:
        self.shell("uiautomator dump /sdcard/xhs_ui.xml")
        xml = self.shell("cat /sdcard/xhs_ui.xml")
        return parse_dump(xml)

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {x} {y}")

    def back(self) -> None:
        self.shell("input keyevent KEYCODE_BACK")

    def swipe_up(self, screen_h: int) -> None:
        x = 630
        y1 = int(screen_h * 0.75) + random.randint(-30, 30)
        y2 = int(screen_h * 0.35) + random.randint(-30, 30)
        self.shell(f"input swipe {x} {y1} {x + random.randint(-20, 20)} {y2} {random.randint(300, 500)}")

    def start_search(self, keyword: str) -> None:
        kw = urllib.parse.quote(keyword, safe="")
        self.shell(f"am start -a android.intent.action.VIEW -d 'xhsdiscover://search/result?keyword={kw}' -p {PKG}")

    def start_shop(self, seller_id: str) -> None:
        self.shell(f"am start -a android.intent.action.VIEW -d 'xhsdiscover://shop_detail?seller_id={seller_id}' -p {PKG}")

    def _top_intent_id(self, pattern: str) -> str | None:
        text = self.shell("dumpsys activity activities")
        m = re.search(r"topResumedActivity=ActivityRecord\{(\w+) ", text)
        return intent_id_from_dumpsys(text, m[1], pattern) if m else None

    def top_goods_id(self) -> str | None:
        """从当前前台 Activity 的启动意图里读取商品 ID。"""
        return self._top_intent_id(r"goods_detail/([0-9a-f]{24})")

    def top_shop_id(self) -> str | None:
        return self._top_intent_id(r"shop_detail\?seller_id=([0-9a-f]{24})")

    def screenshot_bytes(self) -> bytes:
        return self._run("exec-out", "screencap", "-p", binary=True)

    def screenshot(self, path: str) -> None:
        data = self.screenshot_bytes()
        with open(path, "wb") as f:
            f.write(data)


def intent_id_from_dumpsys(text: str, record_id: str, id_pattern: str) -> str | None:
    pat = (r"Hist\s+#\d+: ActivityRecord\{%s [^\n]*\n(?:[^\n]*\n){0,5}?[^\n]*%s"
           % (re.escape(record_id), id_pattern))
    m = re.search(pat, text)
    return m[1] if m else None


def goods_id_from_dumpsys(text: str, record_id: str) -> str | None:
    return intent_id_from_dumpsys(text, record_id, r"goods_detail/([0-9a-f]{24})")


def sleep_jitter(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def _adb(*args: str, timeout: int = 20) -> str:
    r = subprocess.run(["adb", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return (r.stdout + r.stderr).strip()


def list_devices() -> list[str]:
    return [l for l in _adb("devices", "-l").splitlines()[1:] if l.strip()]


def enable_wifi(serial: str | None = None, port: int = 5555) -> str:
    """通过 USB 让手机监听 TCP 端口，返回可 connect 的地址；本机连着 VPN 时局域网可能不可达。"""
    pre = ["-s", serial] if serial else []
    route = _adb(*pre, "shell", "ip route")
    m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", route)
    if not m:
        raise AdbError("读不到手机的 WiFi 地址，请确认手机已连上 WiFi")
    _adb(*pre, "tcpip", str(port))
    return f"{m[1]}:{port}"


def connect_wifi(addr: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+(:\d+)?", addr):
        raise AdbError("地址格式应为 IP 或 IP:端口")
    return _adb("connect", addr)
