import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import httpx

from . import keepawake
from .adb import Adb, AdbError, connect_wifi, enable_wifi, list_devices
from .collector import Abort, Collector, Config, DailyCap, Filters, Stop, write_csv, write_xlsx

COMMANDS = {"search", "shop", "keyword-shops", "dump", "devices", "wifi-on", "connect", "awake"}
ALL_LIMIT = 100000


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--limit", type=int, default=20, help="本次最多采集多少个商品")
    common.add_argument("--all", action="store_true", help="整店或整个结果采集，忽略 --limit（仍受每日上限限制）")
    common.add_argument("--daily-cap", type=int, default=100, help="每天最多点开多少个商品详情")
    common.add_argument("--min-sales", type=int, default=0)
    common.add_argument("--only-with-sales", action="store_true", help="只要已经有销量的商品")
    common.add_argument("--min-price", type=float)
    common.add_argument("--max-price", type=float)
    common.add_argument("--instance", default="Ⅱ·小红书", help="应用分身选择框里要点的名称")
    common.add_argument("--serial", help="设备序列号，只有一台设备时可省略")
    common.add_argument("--out", type=Path, default=Path("data/collector"))
    common.add_argument("--push", metavar="URL", help="采集后调用系统 2，如 http://127.0.0.1:8765")
    common.add_argument("--category", default="未分类")
    common.add_argument("--xlsx", action="store_true", help="同时输出 Excel")
    common.add_argument("--no-keep-awake", action="store_true", help="不在采集期间自动让手机常亮")
    common.add_argument("--max-swipes", type=int, help="每个列表最多下滑多少屏")

    p = argparse.ArgumentParser(prog="xhs_collector", description="用 ADB 在小红书里采集商品 ID")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", parents=[common], help="按关键词在商品标签里采集")
    s.add_argument("keywords", nargs="*", help="关键词，可多个")
    s.add_argument("--keywords-file", type=Path, help="每行一个关键词")
    s.add_argument("--sort-sales", action="store_true", help="商品列表按销量排序")

    h = sub.add_parser("shop", parents=[common], help="按店铺 ID 或店铺名采集整店商品")
    h.add_argument("--id", action="append", default=[], help="店铺 ID（24 位），可重复")
    h.add_argument("--name", action="append", default=[], help="店铺名称，可重复")
    h.add_argument("--sort", choices=["综合", "销量", "新品", "价格"], help="店铺商品排序")
    h.add_argument("--new-days", type=int, help="只采最近 N 天上新的商品（使用“上新”标签）")

    k = sub.add_parser("keyword-shops", parents=[common], help="按关键词找店铺，再逐个采集这些店铺的商品")
    k.add_argument("keywords", nargs="*", help="关键词，可多个")
    k.add_argument("--keywords-file", type=Path, help="每行一个关键词")
    k.add_argument("--shops", type=int, default=3, help="每个关键词取前几家店")
    k.add_argument("--per-shop", type=int, default=10, help="每家店最多采多少个商品")
    k.add_argument("--sort", choices=["综合", "销量", "新品", "价格"], help="店铺商品排序")
    k.add_argument("--new-days", type=int, help="只采最近 N 天上新的商品")

    d = sub.add_parser("dump", help="导出当前界面的控件和截图，用于排查定位问题")
    d.add_argument("--serial")
    d.add_argument("--out", type=Path, default=Path("data/collector"))

    sub.add_parser("devices", help="列出 adb 设备")
    aw = sub.add_parser("awake", help="手动开关手机常亮：on 保存原设置后调到最长息屏并保持充电唤醒，off 恢复，status 查看")
    aw.add_argument("action", choices=["on", "off", "status"])
    aw.add_argument("--serial")
    aw.add_argument("--out", type=Path, default=Path("data/collector"))
    w = sub.add_parser("wifi-on", help="通过 USB 开启手机的 TCP 调试端口并打印连接地址")
    w.add_argument("--serial")
    c = sub.add_parser("connect", help="adb connect IP[:端口]")
    c.add_argument("addr")
    return p


def _keywords(a) -> list[str]:
    words = list(a.keywords)
    if a.keywords_file:
        words += [l.strip() for l in a.keywords_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    return words


def _dump(a) -> int:
    adb = Adb(a.serial)
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    nodes = adb.dump()
    print("前台：", adb.focus())
    for n in nodes:
        print(f"[{n.x1},{n.y1}][{n.x2},{n.y2}] | {n.text[:30]} | {n.desc[:40]}")
    adb.screenshot(str(a.out / f"dump-{stamp}.png"))
    print(f"截图已保存到 {a.out}/dump-{stamp}.png")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "search")
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    try:
        if a.cmd == "devices":
            print("\n".join(list_devices()) or "没有设备")
            return 0
        if a.cmd == "wifi-on":
            print(enable_wifi(a.serial))
            return 0
        if a.cmd == "connect":
            print(connect_wifi(a.addr))
            return 0
        if a.cmd == "dump":
            return _dump(a)
        if a.cmd == "awake":
            adb, state = Adb(a.serial), a.out / "awake.json"
            if a.action == "on":
                print("已保存的原设置：", keepawake.enable(adb, state) or "无变化")
                adb.shell("input keyevent KEYCODE_WAKEUP")
            elif a.action == "off":
                print("已恢复：", keepawake.restore(adb, state) or "没有需要恢复的设置")
            print("当前：", keepawake.status(adb))
            return 0
    except AdbError as e:
        print(f"失败：{e}", file=sys.stderr)
        return 1

    if a.cmd in ("search", "keyword-shops") and not _keywords(a):
        print("请提供关键词或 --keywords-file", file=sys.stderr)
        return 2
    if a.cmd == "shop" and not (a.id or a.name):
        print("请提供 --id 或 --name", file=sys.stderr)
        return 2

    cfg = Config(instance=a.instance, limit=ALL_LIMIT if a.all else a.limit, daily_cap=a.daily_cap,
                 sort_sales=getattr(a, "sort_sales", False), out_dir=a.out, max_swipes=a.max_swipes)
    cap = DailyCap(a.out / "state.json", a.daily_cap)
    if cap.left <= 0:
        print(f"今日已点开 {cap.used} 个商品，达到上限 {a.daily_cap}")
        return 2
    filters = Filters(a.min_sales, a.min_price, a.max_price, a.only_with_sales,
                      getattr(a, "new_days", None))
    show = lambda r: print(f"{r.item_id}  {r.sales_text or '-'}  ¥{r.price}  {r.title[:30]}")

    records, code = [], 0
    try:
        adb = Adb(a.serial)
        col = Collector(adb, cfg)
        with nullcontext() if a.no_keep_awake else keepawake.keep_awake(adb, a.out / "awake.json"):
            if a.cmd == "search":
                for kw in _keywords(a):
                    records += col.run(kw, filters, cap, show)
            elif a.cmd == "keyword-shops":
                for kw in _keywords(a):
                    records += col.run_keyword_shops(kw, filters, cap, show, shops=a.shops,
                                                     per_shop=a.per_shop, new_tab=a.new_days is not None,
                                                     sort=a.sort)
            else:
                new_tab = a.new_days is not None
                for sid in a.id:
                    records += col.run_shop(sid, filters, cap, show, new_tab=new_tab, sort=a.sort)
                for name in a.name:
                    records += col.run_shop_name(name, filters, cap, show, new_tab=new_tab, sort=a.sort)
    except (Abort, AdbError) as e:
        print(f"已停止：{e}", file=sys.stderr)
        code = 1
    except (Stop, KeyboardInterrupt) as e:
        print(f"已按要求停止：{e}")

    if records:
        label = a.keywords[0] if a.cmd in ("search", "keyword-shops") and a.keywords else a.cmd
        base = a.out / f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
        write_csv(base.with_suffix(".csv"), records)
        print(f"已保存 {len(records)} 条到 {base.with_suffix('.csv')}")
        if a.xlsx:
            write_xlsx(base.with_suffix(".xlsx"), records)
            print(f"Excel: {base.with_suffix('.xlsx')}")
        if a.push:
            r = httpx.post(f"{a.push.rstrip('/')}/api/items", timeout=20,
                           json={"text": "\n".join(x.item_id for x in records), "category": a.category})
            print("系统 2 返回：", r.status_code, r.text[:200])
    return code


if __name__ == "__main__":
    sys.exit(main())
