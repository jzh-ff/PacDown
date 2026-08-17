"""SQLite 存储：videos / subscriptions 两张业务表 + 任务状态随 videos 记录。

下载任务与视频记录合一：一条 video 记录即一个任务（status 表达任务状态），
避免双表同步问题；历史与任务列表是同一份数据的不同视图。
"""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

DB_PATH = Path(config.load()["download_dir"]).parent / "data" / "metadata.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,            -- bilibili/douyin/kuaishou/xiaohongshu/generic/...
    video_id TEXT,                     -- 平台内视频唯一 id（BV号/aweme_id/note_id...）
    source_url TEXT NOT NULL,
    title TEXT DEFAULT '',
    author TEXT DEFAULT '',
    author_id TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    cover_path TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,        -- 秒
    publish_time TEXT DEFAULT '',
    stats TEXT DEFAULT '{}',           -- JSON: 播放/点赞/收藏等
    description TEXT DEFAULT '',       -- 作者发布的完整文案
    comments TEXT DEFAULT '',          -- JSON: 抓取的评论列表（可空）
    quality TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    audio_path TEXT DEFAULT '',
    danmaku_path TEXT DEFAULT '',
    images TEXT DEFAULT '[]',          -- 图集文件路径 JSON 数组
    raw_json TEXT DEFAULT '{}',        -- 原始完整元数据
    status TEXT DEFAULT 'pending',     -- pending/parsing/downloading/processing/done/failed
    progress REAL DEFAULT 0,           -- 0~100
    speed TEXT DEFAULT '',
    error TEXT DEFAULT '',
    options TEXT DEFAULT '{}',         -- 下载选项 JSON：quality/extract_audio/download_danmaku
    created_at TEXT NOT NULL,
    downloaded_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_videos_platform ON videos(platform);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_video_id ON videos(platform, video_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    uploader_id TEXT NOT NULL,
    uploader_name TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    last_checked TEXT DEFAULT '',
    last_error TEXT DEFAULT '',
    new_count INTEGER DEFAULT 0,       -- 累计自动抓到的新视频数
    created_at TEXT NOT NULL,
    UNIQUE(platform, uploader_id)
);
"""

_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init() -> None:
    global _conn, DB_PATH
    cfg = config.all_settings()
    DB_PATH = Path(cfg["download_dir"]).parent / "data" / "metadata.db"
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)
    _conn.commit()
    _migrate()


def _migrate() -> None:
    """老库平滑升级：补新列。"""
    for col, ddl in (
        ("description", "ALTER TABLE videos ADD COLUMN description TEXT DEFAULT ''"),
        ("comments", "ALTER TABLE videos ADD COLUMN comments TEXT DEFAULT ''"),
    ):
        try:
            execute(ddl)
        except sqlite3.OperationalError:
            pass  # 列已存在


def conn() -> sqlite3.Connection:
    if _conn is None:
        init()
    return _conn


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        c = conn().execute(sql, params)
        conn().commit()
        return c


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        rows = conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


# ---------------- videos ----------------

def insert_video(v: dict) -> int:
    v = {**v, "created_at": now()}
    cols = ",".join(v.keys())
    marks = ",".join(["?"] * len(v))
    cur = execute(f"INSERT INTO videos({cols}) VALUES({marks})", tuple(v.values()))
    return cur.lastrowid


def update_video(vid: int, **fields) -> None:
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE videos SET {sets} WHERE id=?", (*fields.values(), vid))


def get_video(vid: int) -> dict | None:
    return query_one("SELECT * FROM videos WHERE id=?", (vid,))


def find_by_video_id(platform: str, video_id: str) -> dict | None:
    if not video_id:
        return None
    return query_one(
        "SELECT * FROM videos WHERE platform=? AND video_id=? AND status IN ('done','downloading','pending') ORDER BY id DESC LIMIT 1",
        (platform, video_id),
    )


def active_tasks() -> list[dict]:
    return query(
        "SELECT * FROM videos WHERE status IN ('pending','parsing','downloading','processing') ORDER BY id DESC"
    )


def history(platform: str = "", status: str = "", keyword: str = "",
            page: int = 1, size: int = 24) -> tuple[list[dict], int]:
    where, params = ["1=1"], []
    if platform:
        where.append("platform=?")
        params.append(platform)
    if status:
        where.append("status=?")
        params.append(status)
    if keyword:
        where.append("(title LIKE ? OR author LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    cond = " AND ".join(where)
    total = query_one(f"SELECT COUNT(*) AS n FROM videos WHERE {cond}", tuple(params))["n"]
    rows = query(
        f"SELECT * FROM videos WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, size, (page - 1) * size),
    )
    return rows, total


def stats() -> dict:
    total = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(file_size),0) AS size FROM videos WHERE status='done'")
    today = query_one("SELECT COUNT(*) AS n FROM videos WHERE status='done' AND date(downloaded_at)=date('now','localtime')")
    by_platform = query(
        "SELECT platform, COUNT(*) AS n, COALESCE(SUM(file_size),0) AS size FROM videos WHERE status='done' GROUP BY platform ORDER BY n DESC"
    )
    failed = query_one("SELECT COUNT(*) AS n FROM videos WHERE status='failed'")
    return {
        "total": total["n"], "total_size": total["size"],
        "today": today["n"], "failed": failed["n"],
        "by_platform": by_platform,
    }


def delete_video(vid: int) -> dict | None:
    v = get_video(vid)
    if v:
        execute("DELETE FROM videos WHERE id=?", (vid,))
    return v


# ---------------- subscriptions ----------------

def insert_sub(s: dict) -> int:
    s = {**s, "created_at": now()}
    cols = ",".join(s.keys())
    marks = ",".join(["?"] * len(s))
    cur = execute(f"INSERT OR IGNORE INTO subscriptions({cols}) VALUES({marks})", tuple(s.values()))
    return cur.lastrowid


def list_subs() -> list[dict]:
    return query("SELECT * FROM subscriptions ORDER BY id DESC")


def get_sub(sid: int) -> dict | None:
    return query_one("SELECT * FROM subscriptions WHERE id=?", (sid,))


def update_sub(sid: int, **fields) -> None:
    if fields:
        sets = ",".join(f"{k}=?" for k in fields)
        execute(f"UPDATE subscriptions SET {sets} WHERE id=?", (*fields.values(), sid))


def delete_sub(sid: int) -> None:
    execute("DELETE FROM subscriptions WHERE id=?", (sid,))
