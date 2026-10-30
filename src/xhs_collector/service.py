import logging
import re
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import keepawake
from .adb import Adb, AdbError
from .collector import Abort, Collector, Config, Control, DailyCap, Filters, Stop, write_csv, write_xlsx

SHOP_ID = re.compile(r"^[0-9a-f]{24}$")
MODES = ("search", "shop", "keyword_shops")
SORTS = ("综合", "销量", "新品", "价格")
MAX_LOG = 500


class ParamError(ValueError):
    pass


@dataclass
class Params:
    mode: str = "search"
    keywords: list[str] = field(default_factory=list)
    shop_ids: list[str] = field(default_factory=list)
    shop_names: list[str] = field(default_factory=list)
    limit: int = 20
    all: bool = False
    daily_cap: int = 100
    min_sales: int = 0
    min_price: float | None = None
    max_price: float | None = None
    only_with_sales: bool = False
    sort: str | None = None
    new_days: int | None = None
    shops_per_keyword: int = 3
    per_shop: int = 10
    max_swipes: int | None = None
    instance: str = "Ⅱ·小红书"
    serial: str | None = None
    category: str = "未分类"
    push: bool = True


def _lines(v, name: str, max_len: int = 60, max_n: int = 30) -> list[str]:
    items = [str(x).strip() for x in (v or []) if str(x).strip()]
    if len(items) > max_n or any(len(x) > max_len for x in items):
        raise ParamError(f"{name}最多 {max_n} 个，每个不超过 {max_len} 个字符")
    return items


def _num(d: dict, key: str, default, lo, hi, cast=int):
    v = d.get(key)
    if v in (None, ""):
        return default
    try:
        n = cast(v)
    except (TypeError, ValueError) as e:
        raise ParamError(f"{key} 必须是数字") from e
    if not lo <= n <= hi:
        raise ParamError(f"{key} 需在 {lo} 到 {hi} 之间")
    return n


def parse_params(d: dict) -> Params:
    mode = d.get("mode", "search")
    if mode not in MODES:
        raise ParamError("模式不合法")
    p = Params(mode=mode, keywords=_lines(d.get("keywords"), "关键词", 50, 20),
               shop_ids=_lines(d.get("shop_ids"), "店铺 ID", 24), shop_names=_lines(d.get("shop_names"), "店铺名", 50))
    if any(not SHOP_ID.match(i) for i in p.shop_ids):
        raise ParamError("店铺 ID 必须是 24 位小写十六进制")
    if mode in ("search", "keyword_shops") and not p.keywords:
        raise ParamError("请填写关键词")
    if mode == "shop" and not (p.shop_ids or p.shop_names):
        raise ParamError("请填写店铺 ID 或店铺名")
    p.limit = _num(d, "limit", 20, 1, 2000)
    p.all = bool(d.get("all"))
    p.daily_cap = _num(d, "daily_cap", 100, 1, 2000)
    p.min_sales = _num(d, "min_sales", 0, 0, 10**9)
    p.min_price = _num(d, "min_price", None, 0, 10**6, float)
    p.max_price = _num(d, "max_price", None, 0, 10**6, float)
    p.only_with_sales = bool(d.get("only_with_sales"))
    p.sort = d.get("sort") or None
    if p.sort and p.sort not in SORTS:
        raise ParamError("排序不合法")
    p.new_days = _num(d, "new_days", None, 1, 365)
    p.shops_per_keyword = _num(d, "shops_per_keyword", 3, 1, 50)
    p.per_shop = _num(d, "per_shop", 10, 1, 500)
    p.max_swipes = _num(d, "max_swipes", None, 0, 200)
    p.instance = str(d.get("instance") or "Ⅱ·小红书")[:30]
    p.serial = str(d["serial"])[:40] if d.get("serial") else None
    p.category = str(d.get("category") or "未分类").strip()[:30] or "未分类"
    p.push = bool(d.get("push", True))
    return p


@dataclass
class Job:
    params: Params
    state: str = "running"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    error: str | None = None
    records: list = field(default_factory=list)
    log: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG))
    files: list[str] = field(default_factory=list)


class _LogToJob(logging.Handler):
    def __init__(self, job: Job):
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record):
        self.job.log.append(f"{datetime.now():%H:%M:%S} {record.getMessage()}")


class CollectorService:
    def __init__(self, out_dir: Path, adb_factory=Adb, collector_factory=Collector):
        self.out_dir = Path(out_dir)
        self._adb_factory, self._collector_factory = adb_factory, collector_factory
        self._lock = threading.Lock()
        self.job: Job | None = None

    # ---- 控制 ----
    @property
    def running(self) -> bool:
        return bool(self.job and self.job.state == "running")

    def start(self, params: Params, on_finish=None) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("已有采集任务在运行")
            self.out_dir.mkdir(parents=True, exist_ok=True)
            for name in ("STOP", "PAUSE"):
                (self.out_dir / name).unlink(missing_ok=True)
            self.job = Job(params)
            threading.Thread(target=self._run, args=(self.job, on_finish), daemon=True).start()

    def stop(self) -> None:
        (self.out_dir / "PAUSE").unlink(missing_ok=True)
        (self.out_dir / "STOP").write_text("")

    def pause(self) -> None:
        (self.out_dir / "PAUSE").write_text("")

    def resume(self) -> None:
        (self.out_dir / "PAUSE").unlink(missing_ok=True)

    def status(self) -> dict:
        j = self.job
        if j is None:
            return {"state": "idle", "log": [], "count": 0, "paused": False, "files": []}
        return {"state": j.state, "started_at": j.started_at, "finished_at": j.finished_at, "error": j.error,
                "count": len(j.records), "log": list(j.log), "files": j.files,
                "paused": (self.out_dir / "PAUSE").exists(), "params": asdict(j.params)}

    def records(self) -> list[dict]:
        return [asdict(r) for r in (self.job.records if self.job else [])]

    def export(self, kind: str) -> Path:
        if not self.job or not self.job.records:
            raise ParamError("还没有采集结果")
        path = self.out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-结果.{kind}"
        (write_xlsx if kind == "xlsx" else write_csv)(path, self.job.records)
        return path

    # ---- 执行 ----
    def _run(self, job: Job, on_finish) -> None:
        p = job.params
        handler = _LogToJob(job)
        logger = logging.getLogger("xhs_collector")
        prev_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        say = lambda msg: job.log.append(f"{datetime.now():%H:%M:%S} {msg}")
        on_record = lambda r: (job.records.append(r), say(f"采到 {r.sales_text or '-'}  ¥{r.price}  {r.title[:28]}"))
        final = "error"
        try:
            adb = self._adb_factory(p.serial)
            cfg = Config(instance=p.instance, limit=100000 if p.all else p.limit, daily_cap=p.daily_cap,
                         sort_sales=p.sort == "销量" and p.mode == "search", max_swipes=p.max_swipes,
                         out_dir=self.out_dir)
            cap = DailyCap(self.out_dir / "state.json", p.daily_cap)
            if cap.left <= 0:
                raise Abort(f"今日已点开 {cap.used} 个商品，达到每日上限 {p.daily_cap}")
            filters = Filters(p.min_sales, p.min_price, p.max_price, p.only_with_sales, p.new_days)
            col = self._collector_factory(adb, cfg, control=Control(self.out_dir))
            new_tab = p.new_days is not None
            with keepawake.keep_awake(adb, self.out_dir / "awake.json"):
                if p.mode == "search":
                    for kw in p.keywords:
                        col.run(kw, filters, cap, on_record)
                elif p.mode == "keyword_shops":
                    for kw in p.keywords:
                        col.run_keyword_shops(kw, filters, cap, on_record, shops=p.shops_per_keyword,
                                              per_shop=p.per_shop, new_tab=new_tab, sort=p.sort)
                else:
                    for sid in p.shop_ids:
                        col.run_shop(sid, filters, cap, on_record, new_tab=new_tab, sort=p.sort)
                    for name in p.shop_names:
                        col.run_shop_name(name, filters, cap, on_record, new_tab=new_tab, sort=p.sort)
            final = "done"
            say(f"完成，共采到 {len(job.records)} 个商品")
        except Stop:
            final = "stopped"
            say("已按要求停止")
        except (Abort, AdbError) as e:
            job.error = str(e)
            say(f"已停止：{e}")
        except Exception as e:  # 任何未预料的错误都不能让任务卡在"运行中"
            job.error = f"{type(e).__name__}: {e}"
            say(f"出错：{job.error}")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)
            job.finished_at = datetime.now().isoformat(timespec="seconds")
        if job.records:
            try:
                job.files = [str(self.export("csv")), str(self.export("xlsx"))]
            except Exception as e:
                say(f"保存文件失败：{e}")
            if p.push and on_finish:
                try:
                    on_finish(job.records, p.category)
                    say(f"已把 {len(job.records)} 个商品加入监控")
                except Exception as e:
                    say(f"加入监控失败：{e}")
        job.state = final
