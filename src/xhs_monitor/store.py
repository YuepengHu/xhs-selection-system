import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .increments import Snap

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    title TEXT,
    price REAL,
    shop_sales TEXT,
    category TEXT NOT NULL DEFAULT '未分类',
    created_at TEXT NOT NULL,
    last_fetch_at TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL,
    raw TEXT,
    low INTEGER,
    step INTEGER,
    price REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_item_ts ON snapshots(item_id, ts);
CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    rule TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shops (
    id TEXT PRIMARY KEY,
    name TEXT,
    fans TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS shop_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    raw TEXT NOT NULL,
    low INTEGER NOT NULL,
    step INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shop_snap ON shop_snapshots(shop_id, ts);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    planned INTEGER NOT NULL,
    fetched INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    alerts INTEGER NOT NULL,
    paused INTEGER NOT NULL
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Item:
    id: str
    title: str | None
    price: float | None
    shop_sales: str | None
    category: str
    created_at: datetime
    last_fetch_at: datetime | None
    shop_id: str | None = None
    status: str = "active"


def _item(r: sqlite3.Row) -> Item:
    return Item(
        r["id"], r["title"], r["price"], r["shop_sales"], r["category"],
        datetime.fromisoformat(r["created_at"]),
        datetime.fromisoformat(r["last_fetch_at"]) if r["last_fetch_at"] else None,
        r["shop_id"], r["status"] or "active",
    )


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(items)")}
            if "shop_id" not in cols:
                self._db.execute("ALTER TABLE items ADD COLUMN shop_id TEXT")
            if "status" not in cols:
                self._db.execute("ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def add_item(self, item_id: str, category: str, now: datetime) -> bool:
        cur = self._exec(
            "INSERT OR IGNORE INTO items (id, category, created_at) VALUES (?, ?, ?)",
            (item_id, category or "未分类", _iso(now)),
        )
        return cur.rowcount == 1

    def get_item(self, item_id: str) -> Item | None:
        rows = self._all("SELECT * FROM items WHERE id = ?", (item_id,))
        return _item(rows[0]) if rows else None

    def list_items(self) -> list[Item]:
        return [_item(r) for r in self._all("SELECT * FROM items ORDER BY created_at DESC")]

    def delete_item(self, item_id: str) -> bool:
        return self._exec("DELETE FROM items WHERE id = ?", (item_id,)).rowcount == 1

    def set_status(self, item_id: str, status: str) -> bool:
        return self._exec("UPDATE items SET status = ? WHERE id = ?", (status, item_id)).rowcount == 1

    def set_category(self, item_id: str, category: str) -> None:
        self._exec("UPDATE items SET category = ? WHERE id = ?", (category, item_id))

    def category_counts(self) -> list[dict]:
        rows = self._all("SELECT category AS name, COUNT(*) AS count FROM items GROUP BY category ORDER BY category")
        return [dict(r) for r in rows]

    def rename_category(self, old: str, new: str) -> int:
        return self._exec("UPDATE items SET category = ? WHERE category = ?", (new, old)).rowcount

    def update_info(self, item_id: str, title: str | None, price: float | None,
                    shop_sales: str | None, now: datetime, shop_id: str | None = None) -> None:
        self._exec(
            "UPDATE items SET title = COALESCE(?, title), price = COALESCE(?, price), "
            "shop_sales = COALESCE(?, shop_sales), shop_id = COALESCE(?, shop_id), "
            "last_fetch_at = ? WHERE id = ?",
            (title, price, shop_sales, shop_id, _iso(now), item_id),
        )

    def add_snapshot(self, item_id: str, now: datetime, *, raw: str | None, low: int | None,
                     step: int | None, price: float | None, error: str | None) -> None:
        self._exec(
            "INSERT INTO snapshots (item_id, ts, ok, raw, low, step, price, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, _iso(now), 1 if error is None else 0, raw, low, step, price, error),
        )

    def snapshots(self, item_id: str, since: datetime | None = None) -> list[Snap]:
        sql = "SELECT ts, low, step FROM snapshots WHERE item_id = ? AND ok = 1"
        args: tuple = (item_id,)
        if since is not None:
            sql += " AND ts >= ?"
            args += (_iso(since),)
        rows = self._all(sql + " ORDER BY ts", args)
        return [Snap(datetime.fromisoformat(r["ts"]), r["low"], r["step"]) for r in rows]

    def history(self, item_id: str, limit: int = 500) -> list[dict]:
        rows = self._all(
            "SELECT ts, ok, raw, low, step, price, error FROM snapshots "
            "WHERE item_id = ? ORDER BY ts DESC LIMIT ?", (item_id, limit))
        return [dict(r) for r in rows]

    def snapshots_since(self, since: datetime) -> list[tuple[str, datetime, int, int]]:
        rows = self._all("SELECT item_id, ts, low, step FROM snapshots WHERE ok = 1 AND ts >= ? ORDER BY ts",
                         (_iso(since),))
        return [(r["item_id"], datetime.fromisoformat(r["ts"]), r["low"], r["step"]) for r in rows]

    def last_snapshot(self, item_id: str) -> Snap | None:
        rows = self._all(
            "SELECT ts, low, step FROM snapshots WHERE item_id = ? AND ok = 1 "
            "ORDER BY ts DESC LIMIT 1", (item_id,))
        r = rows[0] if rows else None
        return Snap(datetime.fromisoformat(r["ts"]), r["low"], r["step"]) if r else None

    def last_raw(self, item_id: str) -> str | None:
        rows = self._all(
            "SELECT raw FROM snapshots WHERE item_id = ? AND ok = 1 ORDER BY ts DESC LIMIT 1",
            (item_id,))
        return rows[0]["raw"] if rows else None

    def record_alert(self, item_id: str, rule: str, now: datetime) -> None:
        self._exec("INSERT INTO alerts_sent (item_id, rule, ts) VALUES (?, ?, ?)",
                   (item_id, rule, _iso(now)))

    def alerted_since(self, item_id: str, rule: str, since: datetime) -> bool:
        rows = self._all(
            "SELECT 1 FROM alerts_sent WHERE item_id = ? AND rule = ? AND ts >= ? LIMIT 1",
            (item_id, rule, _iso(since)))
        return bool(rows)

    def paused_until(self) -> datetime | None:
        rows = self._all("SELECT value FROM meta WHERE key = 'paused_until'")
        return datetime.fromisoformat(rows[0]["value"]) if rows else None

    def pause(self, until: datetime) -> None:
        self._exec("INSERT INTO meta (key, value) VALUES ('paused_until', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (_iso(until),))

    def resume(self) -> None:
        self._exec("DELETE FROM meta WHERE key = 'paused_until'")

    def recent_attempts(self, item_id: str, since: datetime) -> list[tuple[datetime, bool]]:
        rows = self._all("SELECT ts, ok FROM snapshots WHERE item_id = ? AND ts >= ? ORDER BY ts",
                         (item_id, _iso(since)))
        return [(datetime.fromisoformat(r["ts"]), bool(r["ok"])) for r in rows]

    def snapshot_at_or_before(self, item_id: str, ts: datetime) -> Snap | None:
        rows = self._all(
            "SELECT ts, low, step FROM snapshots WHERE item_id = ? AND ok = 1 AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1", (item_id, _iso(ts)))
        r = rows[0] if rows else None
        return Snap(datetime.fromisoformat(r["ts"]), r["low"], r["step"]) if r else None

    def log_run(self, now: datetime, planned: int, result) -> None:
        self._exec("INSERT INTO runs (ts, planned, fetched, failed, alerts, paused) VALUES (?, ?, ?, ?, ?, ?)",
                   (_iso(now), planned, result.fetched, result.failed, result.alerts, int(result.paused)))

    def recent_runs(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._all(
            "SELECT ts, planned, fetched, failed, alerts, paused FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        rows = self._all(
            "SELECT a.ts, a.rule, a.item_id, i.title FROM alerts_sent a LEFT JOIN items i ON i.id = a.item_id "
            "ORDER BY a.id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def recent_failures(self, limit: int = 50) -> list[dict]:
        rows = self._all(
            "SELECT s.ts, s.item_id, i.title, s.error FROM snapshots s LEFT JOIN items i ON i.id = s.item_id "
            "WHERE s.ok = 0 ORDER BY s.id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def get_meta(self, key: str) -> str | None:
        rows = self._all("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def upsert_shop(self, shop_id: str, name: str | None, fans: str | None, now: datetime) -> None:
        self._exec(
            "INSERT INTO shops (id, name, fans, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = COALESCE(excluded.name, name), "
            "fans = COALESCE(excluded.fans, fans), updated_at = excluded.updated_at",
            (shop_id, name, fans, _iso(now)))

    def add_shop_snapshot(self, shop_id: str, now: datetime, raw: str, low: int, step: int) -> None:
        self._exec("INSERT INTO shop_snapshots (shop_id, ts, raw, low, step) VALUES (?, ?, ?, ?, ?)",
                   (shop_id, _iso(now), raw, low, step))

    def shop_snapshots(self, shop_id: str, since: datetime | None = None) -> list[Snap]:
        sql, args = "SELECT ts, low, step FROM shop_snapshots WHERE shop_id = ?", (shop_id,)
        if since is not None:
            sql, args = sql + " AND ts >= ?", args + (_iso(since),)
        return [Snap(datetime.fromisoformat(r["ts"]), r["low"], r["step"])
                for r in self._all(sql + " ORDER BY ts", args)]

    def shop_snapshot_at_or_before(self, shop_id: str, ts: datetime) -> Snap | None:
        rows = self._all("SELECT ts, low, step FROM shop_snapshots WHERE shop_id = ? AND ts <= ? "
                         "ORDER BY ts DESC LIMIT 1", (shop_id, _iso(ts)))
        r = rows[0] if rows else None
        return Snap(datetime.fromisoformat(r["ts"]), r["low"], r["step"]) if r else None

    def has_shop_snapshot_since(self, shop_id: str, since: datetime) -> bool:
        return bool(self._all("SELECT 1 FROM shop_snapshots WHERE shop_id = ? AND ts >= ? LIMIT 1",
                              (shop_id, _iso(since))))

    def list_shops(self) -> list[dict]:
        rows = self._all(
            "SELECT s.id, s.name, s.fans, "
            "(SELECT COUNT(*) FROM items i WHERE i.shop_id = s.id) AS item_count, "
            "(SELECT raw FROM shop_snapshots x WHERE x.shop_id = s.id ORDER BY ts DESC LIMIT 1) AS sales, "
            "(SELECT ts FROM shop_snapshots x WHERE x.shop_id = s.id ORDER BY ts DESC LIMIT 1) AS last_ts "
            "FROM shops s ORDER BY s.name")
        return [dict(r) for r in rows]

    def shop_history(self, shop_id: str, limit: int = 400) -> list[dict]:
        rows = self._all("SELECT ts, raw, low, step FROM shop_snapshots WHERE shop_id = ? "
                         "ORDER BY ts DESC LIMIT ?", (shop_id, limit))
        return [dict(r) for r in rows]

    def delete_shop(self, shop_id: str) -> bool:
        self._exec("UPDATE items SET shop_id = NULL WHERE shop_id = ?", (shop_id,))
        return self._exec("DELETE FROM shops WHERE id = ?", (shop_id,)).rowcount == 1

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT INTO meta (key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


COOLDOWN = timedelta(hours=3)
