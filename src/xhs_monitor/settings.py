import json
import os
from dataclasses import dataclass, field

from .alerts import DEFAULT_RULES, Rule
from .store import Store

KEY = "settings"
SECRETS = ("pushplus_token", "dingtalk_webhook", "dingtalk_secret")
MASK = "***"
MAX_RULES = 10


class SettingsError(ValueError):
    pass


@dataclass
class Settings:
    rules: list[Rule] = field(default_factory=lambda: list(DEFAULT_RULES))
    active_start: int = 10
    active_end: int = 23
    pushplus_token: str = ""
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""


def _env_defaults() -> dict:
    return {"pushplus_token": os.getenv("PUSHPLUS_TOKEN", ""),
            "dingtalk_webhook": os.getenv("DINGTALK_WEBHOOK", ""),
            "dingtalk_secret": os.getenv("DINGTALK_SECRET", "")}


def _rules_from(raw: list) -> list[Rule]:
    if not isinstance(raw, list) or len(raw) > MAX_RULES:
        raise SettingsError(f"规则最多 {MAX_RULES} 条")
    out = []
    for r in raw:
        name = str(r.get("name", "")).strip()
        kind = r.get("kind", "hour")
        try:
            delta = int(r.get("min_hour_delta", 1))
            price = None if r.get("min_price") in (None, "") else float(r["min_price"])
            days, daily = int(r.get("days", 3)), int(r.get("min_daily", 1))
        except (TypeError, ValueError) as e:
            raise SettingsError("规则字段不合法") from e
        if kind not in ("hour", "streak"):
            raise SettingsError("规则类型只能是 hour 或 streak")
        if not name or delta < 1 or (price is not None and price < 0):
            raise SettingsError("规则名称不能为空，增量至少为 1，价格不能为负")
        if kind == "streak" and not (2 <= days <= 14 and daily >= 1):
            raise SettingsError("连续增长规则：天数 2 到 14，每天至少新增 1")
        out.append(Rule(name, delta, price, kind, days, daily))
    return out


def load(store: Store) -> Settings:
    data = _env_defaults()
    raw = store.get_meta(KEY)
    if raw:
        data.update(json.loads(raw))
    s = Settings(**{k: v for k, v in data.items() if k != "rules"})
    if "rules" in data:
        s.rules = _rules_from(data["rules"])
    return s


def _dump(s: Settings) -> dict:
    return {"rules": [{"name": r.name, "kind": r.kind, "min_hour_delta": r.min_hour_delta,
                       "min_price": r.min_price, "days": r.days, "min_daily": r.min_daily}
                      for r in s.rules],
            "active_start": s.active_start, "active_end": s.active_end,
            **{k: getattr(s, k) for k in SECRETS}}


def update(store: Store, patch: dict) -> Settings:
    """部分更新；密钥字段传 MASK 或不传表示不变，传空字符串表示清除。"""
    s = load(store)
    if "rules" in patch:
        s.rules = _rules_from(patch["rules"])
    for k in ("active_start", "active_end"):
        if k in patch:
            try:
                setattr(s, k, int(patch[k]))
            except (TypeError, ValueError) as e:
                raise SettingsError("抓取时段必须是整数小时") from e
    if not (0 <= s.active_start <= s.active_end <= 23):
        raise SettingsError("抓取时段需满足 0 <= 开始 <= 结束 <= 23")
    for k in SECRETS:
        if k in patch and patch[k] != MASK:
            setattr(s, k, str(patch[k]).strip())
    if s.dingtalk_webhook and not s.dingtalk_webhook.startswith("https://"):
        raise SettingsError("钉钉地址必须以 https:// 开头")
    store.set_meta(KEY, json.dumps(_dump(s), ensure_ascii=False))
    return s


def public(s: Settings) -> dict:
    d = _dump(s)
    for k in SECRETS:
        d[k] = MASK if d[k] else ""
    return d
