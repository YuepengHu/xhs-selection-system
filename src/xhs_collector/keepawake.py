import json
from contextlib import contextmanager
from pathlib import Path

MAX_TIMEOUT = "2147483647"
STAY_ON_ALL = "7"  # 1=AC 2=USB 4=无线；只设 USB(2) 时，走充电器供电的手机不会常亮
SETTINGS = {
    "screen_off_timeout": ("system", MAX_TIMEOUT),
    "stay_on_while_plugged_in": ("global", STAY_ON_ALL),
}


def _get(adb, key: str, ns: str) -> str:
    return adb.shell(f"settings get {ns} {key}").strip()


def enable(adb, state_path: Path) -> dict[str, str]:
    """保存原值后把息屏时间调到最大，并让充电时保持唤醒覆盖 AC、USB、无线；已保存过原值时不覆盖。"""
    saved = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    for key, (ns, value) in SETTINGS.items():
        current = _get(adb, key, ns)
        if current != value and key not in saved and current.lstrip("-").isdigit():
            saved[key] = current
        adb.shell(f"settings put {ns} {key} {value}")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(saved))
    return saved


def restore(adb, state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    saved = json.loads(state_path.read_text())
    for key, orig in saved.items():
        ns = SETTINGS[key][0]
        adb.shell(f"settings put {ns} {key} {orig}")
    state_path.unlink()
    return saved


def status(adb) -> dict[str, str]:
    return {key: _get(adb, key, ns) for key, (ns, _) in SETTINGS.items()}


@contextmanager
def keep_awake(adb, state_path: Path):
    enable(adb, state_path)
    adb.shell("input keyevent KEYCODE_WAKEUP")
    try:
        yield
    finally:
        restore(adb, state_path)
