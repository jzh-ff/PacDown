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
    tags TEXT DEFAULT '[]',            -- JSON 数组：用户标签
    favorite INTEGER DEFAULT 0,        -- 收藏标记
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
    options TEXT DEFAULT '{}',         -- 每订阅覆盖选项：extract_audio/download_danmaku
    created_at TEXT NOT NULL,
    UNIQUE(platform, uploader_id)
);

CREATE TABLE IF NOT EXISTS reposts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    style TEXT DEFAULT 'natural',
    credit INTEGER DEFAULT 1,
    new_title TEXT DEFAULT '',
    new_desc TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',            -- JSON 数组
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reposts_video ON reposts(video_id);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT DEFAULT 'system',        -- subscription / task / system
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',
    read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(read, id);

CREATE TABLE IF NOT EXISTS tool_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- transcode/compress/trim/gif/watermark/frame/img_*
    src TEXT DEFAULT '',               -- 源描述：video:12 / upload:xx.jpg / images:12
    params TEXT DEFAULT '{}',          -- JSON 参数
    status TEXT DEFAULT 'pending',     -- pending/running/done/failed
    progress REAL DEFAULT 0,
    out_path TEXT DEFAULT '',          -- 产物路径（多产物时为目录或 zip）
    extra TEXT DEFAULT '[]',           -- JSON：多产物路径列表
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT DEFAULT '',
    ua TEXT DEFAULT '',
    device TEXT DEFAULT '',            -- mobile/pc
    os TEXT DEFAULT '',                -- windows/android/ios/macos/linux/other
    browser TEXT DEFAULT '',           -- wechat/qq/chrome/edge/safari/firefox/other
    referer TEXT DEFAULT '',           -- 来源域名（空=直接访问）
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_time ON visits(created_at);
CREATE INDEX IF NOT EXISTS idx_visits_ip ON visits(ip);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    match_type TEXT DEFAULT 'all',     -- all/platform/subscription/tag
    match_value TEXT DEFAULT '',
    actions TEXT DEFAULT '[]',         -- JSON: [{kind, params}]
    enabled INTEGER DEFAULT 1,
    run_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
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
        ("tags", "ALTER TABLE videos ADD COLUMN tags TEXT DEFAULT '[]'"),
        ("favorite", "ALTER TABLE videos ADD COLUMN favorite INTEGER DEFAULT 0"),
        ("options", "ALTER TABLE subscriptions ADD COLUMN options TEXT DEFAULT '{}'"),
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
        "SELECT * FROM videos WHERE status IN ('pending','parsing','working','downloading','processing')"
        " OR (status='duplicate' AND created_at > datetime('now','localtime','-1 minute'))"
        " OR (status='done' AND downloaded_at > datetime('now','localtime','-15 seconds'))"
        " ORDER BY id DESC"
    )


def claim_task(from_status: str, to_status: str) -> dict | None:
    """原子认领一个任务：防止多 worker 同时拾取同一任务重复执行。"""
    with _lock:
        row = conn().execute(
            "SELECT id FROM videos WHERE status=? ORDER BY id LIMIT 1",
            (from_status,)).fetchone()
        if not row:
            return None
        cur = conn().execute(
            "UPDATE videos SET status=? WHERE id=? AND status=?",
            (to_status, row["id"], from_status))
        conn().commit()
        if cur.rowcount == 0:  # 被别的 worker 抢先
            return None
        return dict(conn().execute(
            "SELECT * FROM videos WHERE id=?", (row["id"],)).fetchone())


def _history_where(platform: str = "", status: str = "", keyword: str = "",
                   favorite: int = 0, tag: str = "") -> tuple[str, list]:
    where, params = ["1=1"], []
    if platform:
        where.append("platform=?")
        params.append(platform)
    if status:
        where.append("status=?")
        params.append(status)
    if keyword:
        where.append("(title LIKE ? OR author LIKE ? OR description LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if favorite:
        where.append("favorite=1")
    if tag:
        where.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    return " AND ".join(where), params


def history(platform: str = "", status: str = "", keyword: str = "",
            page: int = 1, size: int = 24,
            favorite: int = 0, tag: str = "") -> tuple[list[dict], int]:
    cond, params = _history_where(platform, status, keyword, favorite, tag)
    total = query_one(f"SELECT COUNT(*) AS n FROM videos WHERE {cond}", tuple(params))["n"]
    rows = query(
        f"SELECT * FROM videos WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, size, (page - 1) * size),
    )
    return rows, total


def history_groups(platform: str = "", status: str = "", keyword: str = "",
                   group_by: str = "date", limit: int = 300,
                   favorite: int = 0, tag: str = "") -> list[dict]:
    """分组视图：按 下载日期 / 平台 / 作者 分组展示。"""
    cond, params = _history_where(platform, status, keyword, favorite, tag)
    rows = query(
        f"SELECT * FROM videos WHERE {cond} ORDER BY downloaded_at DESC, id DESC LIMIT ?",
        (*params, limit))
    groups: dict[str, dict] = {}
    for r in rows:
        if group_by == "date":
            key = (r["downloaded_at"] or r["created_at"] or "")[:10] or "未知日期"
            label = key
        elif group_by == "platform":
            key = r["platform"]
            label = key
        elif group_by == "author":
            key = r["author"] or "未知作者"
            label = key
        else:
            key, label = "", ""
        g = groups.setdefault(key, {"key": key, "label": label, "count": 0,
                                    "size": 0, "items": []})
        g["count"] += 1
        g["size"] += r["file_size"] or 0
        g["items"].append(r)
    return list(groups.values())


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


def list_tags() -> list[str]:
    """聚合全部用户标签（按使用次数降序）。"""
    import json as _json
    from collections import Counter
    counter: Counter = Counter()
    for r in query("SELECT tags FROM videos WHERE tags NOT IN ('', '[]')"):
        try:
            for t in _json.loads(r["tags"] or "[]"):
                t = str(t).strip()
                if t:
                    counter[t] += 1
        except (ValueError, TypeError):
            continue
    return [t for t, _ in counter.most_common(100)]


# ---------------- notifications ----------------

def insert_notification(kind: str, title: str, body: str = "") -> int:
    cur = execute(
        "INSERT INTO notifications(kind, title, body, created_at) VALUES(?,?,?,?)",
        (kind, title, body, now()))
    return cur.lastrowid


def list_notifications(limit: int = 50) -> list[dict]:
    return query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))


def unread_count() -> int:
    return query_one("SELECT COUNT(*) AS n FROM notifications WHERE read=0")["n"]


def mark_notifications_read(ids: list[int] | None = None) -> int:
    if ids:
        marks = ",".join("?" * len(ids))
        cur = execute(f"UPDATE notifications SET read=1 WHERE id IN ({marks})",
                      tuple(ids))
    else:
        cur = execute("UPDATE notifications SET read=1 WHERE read=0")
    return cur.rowcount


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


# ---------------- tool_jobs（工具箱任务） ----------------

def insert_tool_job(j: dict) -> int:
    j = {**j, "created_at": now()}
    cols = ",".join(j.keys())
    marks = ",".join(["?"] * len(j))
    cur = execute(f"INSERT INTO tool_jobs({cols}) VALUES({marks})", tuple(j.values()))
    return cur.lastrowid


def update_tool_job(jid: int, **fields) -> None:
    if fields:
        sets = ",".join(f"{k}=?" for k in fields)
        execute(f"UPDATE tool_jobs SET {sets} WHERE id=?", (*fields.values(), jid))


def get_tool_job(jid: int) -> dict | None:
    return query_one("SELECT * FROM tool_jobs WHERE id=?", (jid,))


def list_tool_jobs(limit: int = 50) -> list[dict]:
    return query("SELECT * FROM tool_jobs ORDER BY id DESC LIMIT ?", (limit,))


def delete_tool_job(jid: int) -> dict | None:
    j = get_tool_job(jid)
    if j:
        execute("DELETE FROM tool_jobs WHERE id=?", (jid,))
    return j


# ---------------- visits（访问统计） ----------------

BOT_UA = ("bot", "spider", "crawl", "curl", "wget", "python-requests", "okhttp")


def parse_ua(ua: str) -> tuple[str, str, str]:
    """从 UA 解析 (device, os, browser)。"""
    low = (ua or "").lower()
    device = "mobile" if any(k in low for k in
                             ("mobile", "android", "iphone", "ipad", "harmonyos")) else "pc"
    if "windows" in low:
        os_name = "windows"
    elif "android" in low:
        os_name = "android"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "ios"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macos"
    elif "linux" in low:
        os_name = "linux"
    else:
        os_name = "other"
    if "micromessenger" in low:
        browser = "wechat"
    elif "qq/" in low or "qqbrowser" in low:
        browser = "qq"
    elif "edg" in low:
        browser = "edge"
    elif "chrome" in low or "crios" in low:
        browser = "chrome"
    elif "safari" in low:
        browser = "safari"
    elif "firefox" in low:
        browser = "firefox"
    elif "douyin" in low or "aweme" in low:
        browser = "douyin"
    else:
        browser = "other"
    return device, os_name, browser


def insert_visit(ip: str, ua: str, referer: str) -> int | None:
    """记录一次页面访问；爬虫 UA 与 30 分钟内同 ip+ua 重复的跳过。返回 id 或 None。"""
    low = (ua or "").lower()
    if any(b in low for b in BOT_UA):
        return None
    recent = query_one(
        "SELECT id FROM visits WHERE ip=? AND ua=? AND created_at > "
        "datetime('now','localtime','-30 minutes') LIMIT 1", (ip, ua))
    if recent:
        return None
    device, os_name, browser = parse_ua(ua)
    cur = execute(
        "INSERT INTO visits(ip, ua, device, os, browser, referer, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (ip, ua[:300], device, os_name, browser, referer, now()))
    return cur.lastrowid


def visit_stats(days: int = 30) -> dict:
    """访问聚合：总览 / 按天 / 24h 时段 / 设备与来源分布 / 最近明细。"""
    def one(sql, params=()):
        return query_one(sql, params) or {}

    total = one("SELECT COUNT(*) AS pv, COUNT(DISTINCT ip||ua) AS uv FROM visits")
    today = one("SELECT COUNT(*) AS pv, COUNT(DISTINCT ip||ua) AS uv FROM visits "
                "WHERE date(created_at)=date('now','localtime')")
    yesterday = one("SELECT COUNT(*) AS pv FROM visits "
                    "WHERE date(created_at)=date('now','localtime','-1 day')")
    by_day = query(
        "SELECT date(created_at) AS d, COUNT(*) AS pv, COUNT(DISTINCT ip||ua) AS uv "
        "FROM visits WHERE created_at > datetime('now','localtime',?) "
        "GROUP BY d ORDER BY d", (f"-{days} days",))
    by_hour = query(
        "SELECT CAST(strftime('%H', created_at) AS INTEGER) AS h, COUNT(*) AS n "
        "FROM visits WHERE created_at > datetime('now','localtime','-30 days') "
        "GROUP BY h ORDER BY h")
    by_device = query(
        "SELECT device AS k, COUNT(*) AS n FROM visits GROUP BY device ORDER BY n DESC")
    by_os = query("SELECT os AS k, COUNT(*) AS n FROM visits GROUP BY os ORDER BY n DESC")
    by_browser = query(
        "SELECT browser AS k, COUNT(*) AS n FROM visits GROUP BY browser ORDER BY n DESC")
    by_referer = query(
        "SELECT CASE WHEN referer='' THEN '直接访问' ELSE referer END AS k, "
        "COUNT(*) AS n FROM visits GROUP BY k ORDER BY n DESC LIMIT 10")
    recent = query(
        "SELECT ip, ua, device, os, browser, referer, created_at FROM visits "
        "ORDER BY id DESC LIMIT 100")
    return {
        "total_pv": total.get("pv", 0), "total_uv": total.get("uv", 0),
        "today_pv": today.get("pv", 0), "today_uv": today.get("uv", 0),
        "yesterday_pv": yesterday.get("pv", 0),
        "by_day": by_day, "by_hour": by_hour,
        "by_device": by_device, "by_os": by_os, "by_browser": by_browser,
        "by_referer": by_referer, "recent": recent,
    }


# ---------------- rules（自动后处理规则） ----------------

def insert_rule(r: dict) -> int:
    r = {**r, "created_at": now()}
    cols = ",".join(r.keys())
    marks = ",".join(["?"] * len(r))
    cur = execute(f"INSERT INTO rules({cols}) VALUES({marks})", tuple(r.values()))
    return cur.lastrowid


def list_rules() -> list[dict]:
    return query("SELECT * FROM rules ORDER BY id DESC")


def get_rule(rid: int) -> dict | None:
    return query_one("SELECT * FROM rules WHERE id=?", (rid,))


def update_rule(rid: int, **fields) -> None:
    if fields:
        sets = ",".join(f"{k}=?" for k in fields)
        execute(f"UPDATE rules SET {sets} WHERE id=?", (*fields.values(), rid))


def delete_rule(rid: int) -> None:
    execute("DELETE FROM rules WHERE id=?", (rid,))


# ---------------- reposts（搬运文案） ----------------

def insert_repost(r: dict) -> int:
    r = {**r, "created_at": now()}
    cols = ",".join(r.keys())
    marks = ",".join(["?"] * len(r))
    cur = execute(f"INSERT INTO reposts({cols}) VALUES({marks})", tuple(r.values()))
    return cur.lastrowid


def list_reposts(video_id: int | None = None, limit: int = 30) -> list[dict]:
    if video_id:
        return query("SELECT * FROM reposts WHERE video_id=? ORDER BY id DESC LIMIT ?",
                     (video_id, limit))
    return query("SELECT * FROM reposts ORDER BY id DESC LIMIT ?", (limit,))
