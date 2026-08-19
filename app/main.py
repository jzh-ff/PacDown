"""FastAPI 应用：全部路由 + 生命周期管理。"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import config, database, postprocess, scheduler, toolbox
from .downloader import manager, remove_video_files
from .parsers import PLATFORM_META, extract_urls
from .parsers import bilibili as bili_parser
from .parsers import douyin as dy_parser
from .parsers.base import ParseError
from .parsers.http_download import REFERERS, safe_filename

# PyInstaller onefile：静态资源在解包目录 _MEIPASS 下；源码运行在项目 static/
if getattr(sys, "frozen", False):
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "static"
else:
    STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="PacDown", docs_url=None, redoc_url=None)


# ---------------- 认证：多用户（httpOnly cookie 会话） ----------------

AUTH_COOKIE = "pacdown_session"


def current_user(request: Request) -> dict:
    """FastAPI 依赖：未登录抛 401。"""
    token = request.cookies.get(AUTH_COOKIE) or ""
    u = database.session_user(token)
    if not u:
        raise HTTPException(401, "请先登录")
    return u


def admin_only(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "仅管理员可操作")
    return user


@app.post("/api/auth/register")
async def register(req: Request):
    body = await req.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fa5]{2,20}", username):
        raise HTTPException(400, "用户名需为 2-20 位字母/数字/下划线/中文")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if not config.get("allow_register", True):
        raise HTTPException(403, "注册已关闭，请联系管理员创建账号")
    first = database.user_count() == 0
    role = "admin" if first else "user"
    uid = database.create_user(username, password, role)
    if not uid:
        raise HTTPException(409, "用户名已被占用")
    if first:
        adopted = database.adopt_orphan_data(uid)  # 首个 admin 继承存量数据
    token = database.create_session(uid)
    resp = JSONResponse({"ok": True, "user": {"id": uid, "username": username,
                                              "role": role}})
    resp.set_cookie(AUTH_COOKIE, token, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600, path="/")
    return resp


@app.post("/api/auth/login")
async def login(req: Request):
    body = await req.json()
    u = database.verify_user((body.get("username") or "").strip(),
                             body.get("password") or "")
    if not u:
        raise HTTPException(401, "用户名或密码错误")
    token = database.create_session(u["id"])
    resp = JSONResponse({"ok": True, "user": {"id": u["id"], "username": u["username"],
                                              "role": u["role"]}})
    resp.set_cookie(AUTH_COOKIE, token, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600, path="/")
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    database.delete_session(request.cookies.get(AUTH_COOKIE) or "")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request):
    token = request.cookies.get(AUTH_COOKIE) or ""
    u = database.session_user(token)
    if not u:
        return {"user": None}
    return {"user": u, "allow_register": bool(config.get("allow_register", True))}


@app.post("/api/auth/password")
async def change_password(request: Request, user: dict = Depends(current_user)):
    body = await req.json()
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    if len(new) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if not database.verify_user(user["username"], old):
        raise HTTPException(401, "原密码错误")
    database.update_password(user["id"], new)
    return {"ok": True}


@app.middleware("http")
async def _record_visit(request: Request, call_next):
    """访问统计：仅记页面级访问（GET /），X-Forwarded-For 兼容反代。"""
    response = await call_next(request)
    try:
        if request.method == "GET" and request.url.path == "/":
            ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
                or (request.client.host if request.client else "")
            ref = request.headers.get("referer") or ""
            ref_domain = urlparse(ref).netloc.lower().replace("www.", "")
            database.insert_visit(ip, request.headers.get("user-agent", ""), ref_domain)
    except Exception:
        pass  # 统计失败不影响请求
    return response


@app.on_event("startup")
def _startup():
    config.load()
    database.init()
    manager.start()
    scheduler.sub_scheduler.start()
    toolbox.tool_manager.start()


@app.on_event("shutdown")
def _shutdown():
    manager.stop()
    toolbox.tool_manager.stop()


# ---------------- 基础 ----------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/sw.js")
def sw_js():
    """Service Worker 挂根路径，获得全站作用域（/static/ 下默认作用域不够）。"""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/api/platforms")
def platforms(user: dict = Depends(current_user)):
    return {"platforms": PLATFORM_META,
            "ffmpeg": postprocess.ffmpeg_available()}


# ---------------- 解析 / 下载 ----------------

@app.post("/api/parse")
async def parse(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    urls = extract_urls(body.get("text") or "")
    results = []
    for u in urls[:30]:
        try:
            results.append(_parse_one(u))
        except Exception as e:
            results.append({"url": u, "ok": False, "error": str(e)[:200]})
    return {"results": results}


def _uploader_url(u: str) -> tuple[str, str] | None:
    """识别博主主页链接，返回 (platform, uploader_id)。"""
    m = re.search(r"space\.bilibili\.com/(\d+)", u.lower())
    if m:
        return "bilibili", m.group(1)
    m = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", u)
    if m:
        return "douyin", m.group(1)
    return None


def _parse_one(u: str) -> dict:
    """单个链接的解析分发：主页 / 合集 / 普通视频。"""
    # 博主主页 → 视频列表批量
    up = _uploader_url(u)
    if up:
        platform, uid = up
        info = scheduler.fetch_uploader_info(platform, uid)
        videos = scheduler.fetch_uploader_videos(platform, uid, limit=50)
        return {"url": u, "ok": True, "kind": "uploader",
                "uploader": {"platform": platform, "id": uid, **info},
                "entries": videos}

    # 抖音合集页 → 合集视频列表
    if "douyin.com/collection/" in u.lower():
        mix_id = dy_parser._extract_mix_id(u)
        if mix_id:
            mix = dy_parser.list_mix(mix_id, limit=50)
            return {"url": u, "ok": True, "kind": "playlist",
                    "title": mix["name"] or "抖音合集",
                    "entries": mix["entries"]}

    # 常规单视频解析
    info = manager.parse_preview(u)
    item = {"url": u, "ok": True, "info": info}

    # B站：顺带列出分P与合集
    if info.get("platform") == "bilibili":
        rel = bili_parser.list_related(u)
        sections = []
        if rel["parts"]:
            sections.append({"kind": "parts",
                             "label": f"分P · 共 {len(rel['parts'])} 部分",
                             "entries": rel["parts"]})
        if rel["season"] and len(rel["season"]["entries"]) > 1:
            n = len(rel["season"]["entries"])
            sections.append({"kind": "season",
                             "label": f"合集《{rel['season']['title']}》· {n} 集",
                             "entries": rel["season"]["entries"]})
        if sections:
            item["kind"] = "playlist"
            item["sections"] = sections

    # 抖音单视频属于合集 → 给前端一个懒加载入口
    if info.get("collection"):
        item["collection"] = info["collection"]
    return item


@app.get("/api/collection")
def collection(platform: str = "", id: str = "", user: dict = Depends(current_user)):
    """懒加载合集完整列表（前端点击「下载整个合集」时调用）。"""
    if platform == "douyin" and id:
        try:
            mix = dy_parser.list_mix(id, limit=100)
        except ParseError as e:
            raise HTTPException(502, str(e))
        return {"name": mix["name"], "entries": mix["entries"]}
    raise HTTPException(400, "该平台暂不支持合集列表")


@app.post("/api/download")
async def download(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    urls = extract_urls(body.get("text") or "")
    if not urls:
        raise HTTPException(400, "未识别到链接")
    if len(urls) > 50:
        raise HTTPException(400, "单次最多 50 个链接")
    options = body.get("options") or {}
    options.setdefault("quality", config.get("default_quality", "best"))
    options.setdefault("extract_audio", bool(config.get("extract_audio", False)))
    options.setdefault("download_danmaku", bool(config.get("download_danmaku", False)))
    options.setdefault("download_subtitle", bool(config.get("download_subtitle", False)))
    force = bool(body.get("force", False))

    results = []
    for u in urls[:50]:
        try:
            r = manager.create_task(u, options, force=force,
                                    user_id=user["id"], username=user["username"])
            results.append({"url": u, "status": "queued", "id": r["id"]})
        except Exception as e:
            results.append({"url": u, "status": "failed", "error": str(e)[:200]})
    return {"results": results}


@app.get("/api/tasks")
def tasks(user: dict = Depends(current_user)):
    return {"tasks": [_task_view(t) for t in database.active_tasks(user["id"])]}


def _own_video(vid: int, user: dict) -> dict:
    v = database.get_video(vid)
    if not v:
        raise HTTPException(404, "任务不存在")
    if v.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作该记录")
    return v


@app.post("/api/tasks/{vid}/retry")
def retry_task(vid: int, user: dict = Depends(current_user)):
    _own_video(vid, user)
    if not manager.retry(vid):
        raise HTTPException(400, "仅失败的任务可重试")
    return {"ok": True}


@app.delete("/api/tasks/{vid}")
def dismiss_task(vid: int, user: dict = Depends(current_user)):
    """清理 duplicate 提示记录（前端展示后调用）。"""
    v = _own_video(vid, user)
    if v["status"] not in ("duplicate", "failed"):
        raise HTTPException(400, "仅提示类任务可移除")
    database.delete_video(vid)
    return {"ok": True}


@app.post("/api/tasks/clear")
def clear_tasks(user: dict = Depends(current_user)):
    """一键清理本人全部失败/重复提示任务。"""
    cur = database.execute(
        "DELETE FROM videos WHERE user_id=? AND status IN ('failed','duplicate')",
        (user["id"],))
    return {"ok": True, "removed": cur.rowcount}


@app.post("/api/tasks/retry_all")
def retry_all_tasks(user: dict = Depends(current_user)):
    """本人全部失败任务重新入队。"""
    rows = database.query("SELECT id FROM videos WHERE user_id=? AND status='failed'",
                          (user["id"],))
    n = 0
    for r in rows:
        if manager.retry(r["id"]):
            n += 1
    return {"ok": True, "retried": n}


def _task_view(t: dict) -> dict:
    return {k: t[k] for k in (
        "id", "platform", "title", "author", "cover_url", "status",
        "progress", "speed", "error", "file_path", "duration",
        "created_at", "downloaded_at")}


# ---------------- 历史 ----------------

@app.get("/api/history")
def history(platform: str = "", status: str = "", keyword: str = "",
            page: int = 1, size: int = 24, favorite: int = 0, tag: str = "",
            user: dict = Depends(current_user)):
    rows, total = database.history(platform, status, keyword, page, size,
                                   favorite=favorite, tag=tag, user_id=user["id"])
    return {"items": [_row_public(r) for r in rows], "total": total,
            "page": page, "size": size}


@app.get("/api/history/groups")
def history_groups(platform: str = "", status: str = "", keyword: str = "",
                   group_by: str = "date", favorite: int = 0, tag: str = "",
                   user: dict = Depends(current_user)):
    if group_by not in ("date", "platform", "author"):
        raise HTTPException(400, "group_by 仅支持 date/platform/author")
    groups = database.history_groups(platform, status, keyword, group_by,
                                     favorite=favorite, tag=tag, user_id=user["id"])
    return {"groups": [
        {"key": g["key"], "label": g["label"], "count": g["count"],
         "size": g["size"], "items": [_row_public(r) for r in g["items"]]}
        for g in groups
    ]}


@app.get("/api/history/tags")
def history_tags(user: dict = Depends(current_user)):
    return {"tags": database.list_tags(user["id"])}


@app.get("/api/history/stats")
def history_stats(user: dict = Depends(current_user)):
    return database.stats(user["id"])


@app.get("/api/history/export")
def export_csv(ids: str = "", user: dict = Depends(current_user)):
    """导出 CSV；ids 为逗号分隔的 id 列表时只导出选中项（限本人）。"""
    if ids.strip():
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:500]
        rows = [r for r in (database.get_video(i) for i in id_list)
                if r and r.get("user_id") == user["id"]]
    else:
        rows, _ = database.history(size=100000, page=1, user_id=user["id"])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "平台", "标题", "作者", "时长(秒)", "发布时间",
                     "清晰度", "标签", "收藏", "文件路径", "大小(字节)", "下载时间", "状态"])
    for r in rows:
        writer.writerow([r["id"], r["platform"], r["title"], r["author"],
                         r["duration"], r["publish_time"], r["quality"],
                         " ".join(json.loads(r.get("tags") or "[]")),
                         "是" if r.get("favorite") else "",
                         r["file_path"], r["file_size"], r["downloaded_at"],
                         r["status"]])
    data = buf.getvalue().encode("utf-8-sig")
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=history.csv"})


@app.get("/api/history/{vid}")
def history_detail(vid: int, user: dict = Depends(current_user)):
    v = _own_video(vid, user)
    return _row_public(v, full=True)


@app.patch("/api/history/{vid}")
async def patch_history(vid: int, req: Request, user: dict = Depends(current_user)):
    """更新标签 / 收藏 / 标题 / 作者 / 描述等用户字段。"""
    v = _own_video(vid, user)
    body = await req.json()
    fields = {}
    if "tags" in body:
        tags = body["tags"]
        if not isinstance(tags, list):
            raise HTTPException(400, "tags 需为数组")
        tags = [str(t).strip()[:30] for t in tags if str(t).strip()][:20]
        fields["tags"] = json.dumps(tags, ensure_ascii=False)
    if "favorite" in body:
        fields["favorite"] = 1 if body["favorite"] else 0
    for k in ("title", "author", "description"):
        if k in body:
            fields[k] = str(body[k] or "").strip()[:500]
    if fields:
        database.update_video(vid, **fields)
    return {"ok": True, **fields}


@app.delete("/api/history/{vid}")
def delete_history(vid: int, keep_files: bool = True, user: dict = Depends(current_user)):
    v = _own_video(vid, user)
    database.delete_video(vid)
    removed = [] if keep_files else remove_video_files(v)
    return {"ok": True, "files_removed": removed}


@app.post("/api/history/batch_delete")
async def batch_delete(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    ids = [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:500]
    keep_files = bool(body.get("keep_files", True))
    if not ids:
        raise HTTPException(400, "未选择记录")
    deleted, files_removed = 0, []
    for vid in ids:
        v = _own_video(vid, user)
        database.delete_video(vid)
        deleted += 1
        if not keep_files:
            files_removed += remove_video_files(v)
    return {"ok": True, "deleted": deleted,
            "files_removed": len(files_removed)}


@app.post("/api/history/{vid}/open")
def open_folder(vid: int, user: dict = Depends(current_user)):
    v = _own_video(vid, user)
    if not v or not v.get("file_path"):
        raise HTTPException(404, "文件不存在")
    p = Path(v["file_path"])
    if not p.exists():
        raise HTTPException(404, "文件已被移动或删除")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return {"ok": True}


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@app.get("/api/history/{vid}/zip")
def history_zip(vid: int, user: dict = Depends(current_user)):
    """把该记录的全部产物（视频/图集/封面/音频/弹幕/sidecar）打成 ZIP 供浏览器保存到本机。"""
    v = _own_video(vid, user)
    if v["status"] != "done":
        raise HTTPException(400, "任务尚未完成")
    candidates: list[str] = []
    for key in ("file_path", "cover_path", "audio_path", "danmaku_path", "subtitle_path"):
        candidates.append(v.get(key) or "")
    candidates += json.loads(v.get("images") or "[]")
    if v.get("file_path"):
        sidecar = Path(v["file_path"]).with_suffix(".json")
        candidates.append(str(sidecar))
    files, seen = [], set()
    for c in candidates:
        if c and c not in seen and Path(c).exists():
            seen.add(c)
            files.append(Path(c))
    if not files:
        raise HTTPException(404, "文件已被移动或删除")
    tmp_dir = database.DB_PATH.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(tmp_dir))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:  # 媒体已压缩，用 STORED 省CPU
            for f in files:
                zf.write(f, f.name)
    except Exception:
        _silent_unlink(tmp)
        raise HTTPException(500, "打包失败")
    name = safe_filename(v.get("title") or f"pacdown_{vid}", 60)
    return FileResponse(tmp, filename=f"{name}.zip", media_type="application/zip",
                        background=BackgroundTask(_silent_unlink, tmp))


def _row_public(r: dict, full: bool = False) -> dict:
    keys = ["id", "platform", "video_id", "title", "description", "author",
            "cover_url", "cover_path", "duration", "publish_time", "quality",
            "file_path", "file_size", "audio_path", "danmaku_path", "subtitle_path",
            "images", "status", "progress", "error", "created_at", "downloaded_at",
            "favorite"]
    out = {k: r.get(k, "") for k in keys}
    out["stats"] = json.loads(r.get("stats") or "{}")
    try:
        out["comments"] = json.loads(r.get("comments") or "[]")
    except json.JSONDecodeError:
        out["comments"] = []
    try:
        out["tags"] = json.loads(r.get("tags") or "[]")
    except json.JSONDecodeError:
        out["tags"] = []
    if full:
        out["raw"] = json.loads(r.get("raw_json") or "{}")
        out["source_url"] = r.get("source_url", "")
    return out


# ---------------- 本地文件预览（带 Range，支持视频拖动进度条） ----------------

MIME = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".mov": "video/quicktime", ".flv": "video/x-flv", ".ts": "video/mp2t",
        ".m4v": "video/mp4", ".avi": "video/x-msvideo",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
        ".flac": "audio/flac", ".zip": "application/zip", ".pdf": "application/pdf"}


@app.get("/api/file")
def serve_file(request: Request, id: int, type: str = "video", index: int = 0,
               download: int = 0, user: dict = Depends(current_user)):
    """按记录 ID 输出已下载文件。路径取自数据库而非用户输入，杜绝目录遍历。

    download=1 时带 Content-Disposition 附件头，浏览器直接保存到本机。
    """
    v = _own_video(id, user)
    if not v:
        raise HTTPException(404, "记录不存在")
    if type == "image":
        images = json.loads(v.get("images") or "[]")
        if index >= len(images):
            raise HTTPException(404, "图片不存在")
        path = Path(images[index])
    elif type == "audio":
        path = Path(v.get("audio_path") or "")
    elif type == "subtitle":
        path = Path(v.get("subtitle_path") or "")
    elif type == "cover":
        path = Path(v.get("cover_path") or "")
    else:
        path = Path(v.get("file_path") or "")
    if not str(path):
        raise HTTPException(404, "文件不存在")
    if not path.exists():
        raise HTTPException(404, "文件已被移动或删除")

    media_type = MIME.get(path.suffix.lower(), "application/octet-stream")
    size = path.stat().st_size
    range_header = request.headers.get("range")
    extra = {"Accept-Ranges": "bytes",
             "Cache-Control": "private, max-age=3600"}
    if download:
        extra["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(path.name)}"

    if range_header and range_header.startswith("bytes="):
        try:
            start_s, _, end_s = range_header[6:].partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416,
                                headers={"Content-Range": f"bytes */{size}"})
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
            return Response(content=chunk, status_code=206, media_type=media_type,
                            headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                     **extra})
        except ValueError:
            pass

    def iter_file():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 18)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(iter_file(), media_type=media_type,
                             headers={**extra, "Content-Length": str(size)})


# ---------------- 封面代理（绕防盗链） ----------------

@app.get("/api/cover")
def cover(url: str, platform: str = ""):
    referer = REFERERS.get(platform, "")
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as c:
            r = c.get(url, headers=headers)
        return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"))
    except Exception:
        raise HTTPException(502, "封面加载失败")


# ---------------- 配置 ----------------

SENSITIVE = ("bilibili_cookie", "douyin_cookie", "kuaishou_cookie",
             "xiaohongshu_cookie", "ai_api_key", "admin_key")


@app.get("/api/config")
def get_config(user: dict = Depends(admin_only)):
    cfg = config.all_settings()
    for k in SENSITIVE:
        cfg[k] = "__SET__" if cfg.get(k) else ""
    cfg["ffmpeg"] = postprocess.ffmpeg_available()
    cfg["download_dir"] = config.get("download_dir")
    return cfg


@app.post("/api/config")
async def set_config(req: Request, user: dict = Depends(admin_only)):
    body = await req.json()
    patch = {}
    for k, v in body.items():
        if k in SENSITIVE and (v == "__KEEP__" or v is None):
            continue  # 前端占位值：保持不变
        if v == "__KEEP__":
            continue
        patch[k] = v
    cfg = config.update(patch)
    if "subscription_interval" in patch:
        scheduler.sub_scheduler.restart()
    for k in SENSITIVE:
        cfg[k] = "__SET__" if cfg.get(k) else ""
    return cfg


@app.get("/api/config/dirs")
def get_dirs(user: dict = Depends(current_user)):
    return {"current": config.get("download_dir"),
            "recent": config.get("recent_dirs", [])}


@app.post("/api/config/dirs")
async def set_dir(req: Request, user: dict = Depends(admin_only)):
    body = await req.json()
    d = (body.get("dir") or "").strip()
    if not d:
        raise HTTPException(400, "目录不能为空")
    try:
        cfg = config.set_download_dir(d)
    except OSError as e:
        raise HTTPException(400, f"目录不可用：{e}")
    return {"current": cfg["download_dir"], "recent": cfg.get("recent_dirs", [])}


# ---------------- 订阅 ----------------

@app.get("/api/subscriptions")
def list_subs(user: dict = Depends(current_user)):
    return {"items": database.list_subs(user["id"])}


@app.post("/api/subscriptions")
async def add_sub(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "请输入博主主页链接")
    try:
        platform, uploader_id = scheduler.parse_uploader_url(url)
        info = scheduler.fetch_uploader_info(platform, uploader_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"获取博主信息失败：{str(e)[:150]}")
    options = {
        "extract_audio": bool(body.get("extract_audio", False)),
        "download_danmaku": bool(body.get("download_danmaku", False)),
    }
    sid = database.insert_sub({
        "platform": platform, "uploader_id": uploader_id,
        "uploader_name": info["name"], "avatar_url": info["avatar"],
        "source_url": url, "enabled": 1,
        "options": json.dumps(options, ensure_ascii=False),
    }, user_id=user["id"])
    if not sid:
        raise HTTPException(409, "该博主已在订阅列表中")
    return {"ok": True, "id": sid}


def _own_sub(sid: int, user: dict) -> dict:
    sub = database.get_sub(sid)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    if sub.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作该订阅")
    return sub


@app.patch("/api/subscriptions/{sid}")
async def patch_sub(sid: int, req: Request, user: dict = Depends(current_user)):
    sub = _own_sub(sid, user)
    body = await req.json()
    fields = {}
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
    if "options" in body and isinstance(body["options"], dict):
        cur = json.loads(sub.get("options") or "{}")
        cur.update({k: bool(v) for k, v in body["options"].items()
                    if k in ("extract_audio", "download_danmaku")})
        fields["options"] = json.dumps(cur, ensure_ascii=False)
    if fields:
        database.update_sub(sid, **fields)
    return {"ok": True}


@app.delete("/api/subscriptions/{sid}")
def del_sub(sid: int, user: dict = Depends(current_user)):
    _own_sub(sid, user)
    database.delete_sub(sid)
    return {"ok": True}


@app.post("/api/subscriptions/{sid}/check")
def check_sub(sid: int, user: dict = Depends(current_user)):
    sub = _own_sub(sid, user)
    try:
        n = scheduler.check_subscription(sub)
    except Exception as e:
        raise HTTPException(502, str(e)[:200])
    return {"ok": True, "new_count": n}


# ---------------- 工具箱 ----------------

@app.get("/api/toolbox/tools")
def toolbox_tools(user: dict = Depends(current_user)):
    return {"tools": toolbox.TOOLS,
            "video_tools": sorted(toolbox.VIDEO_TOOLS),
            "image_tools": sorted(toolbox.IMAGE_TOOLS),
            "ffmpeg": postprocess.ffmpeg_available()}


@app.post("/api/toolbox/upload")
async def toolbox_upload(file: UploadFile, user: dict = Depends(current_user)):
    data = await file.read()
    if len(data) > 500 * 1024 * 1024:
        raise HTTPException(400, "文件超过 500MB 限制")
    try:
        return toolbox.save_upload(file.filename or "upload.bin", data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/toolbox/sources")
def toolbox_sources(keyword: str = "", user: dict = Depends(current_user)):
    """片库中可作为工具素材的记录：本人已完成视频 + 图集。"""
    rows, _ = database.history(status="done", keyword=keyword, page=1, size=50,
                               user_id=user["id"])
    items = []
    for r in rows:
        images = json.loads(r.get("images") or "[]")
        if not r.get("file_path") and not images:
            continue
        items.append({
            "id": r["id"], "title": r["title"], "author": r["author"],
            "platform": r["platform"], "cover_url": r["cover_url"],
            "cover_path": r.get("cover_path") or "",
            "kind": "images" if images else "video",
            "image_count": len(images),
        })
    return {"items": items}


@app.post("/api/toolbox/jobs")
async def toolbox_create(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    kind = body.get("kind") or ""
    video_id = int(body.get("video_id") or 0)
    upload = (body.get("upload") or "").strip()
    if not video_id and not upload:
        raise HTTPException(400, "请先选择素材")
    src = f"video:{video_id}" if video_id else f"upload:{upload}"
    try:
        jid = toolbox.tool_manager.create(kind, src, body.get("params") or {},
                                          user_id=user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": jid}


@app.get("/api/toolbox/jobs")
def toolbox_jobs(user: dict = Depends(current_user)):
    rows = database.list_tool_jobs(user["id"], 50)
    return {"items": [{
        "id": r["id"], "kind": r["kind"], "src": r["src"],
        "status": r["status"], "progress": r["progress"],
        "error": r["error"], "created_at": r["created_at"],
        "finished_at": r["finished_at"],
        "has_output": bool(r["out_path"]),
        "outputs": len(json.loads(r.get("extra") or "[]")) or (1 if r["out_path"] else 0),
    } for r in rows]}


@app.get("/api/toolbox/jobs/{jid}/file")
def toolbox_file(jid: int, index: int = 0, user: dict = Depends(current_user)):
    job = database.get_tool_job(jid)
    if not job or job["status"] != "done" or job.get("user_id") != user["id"]:
        raise HTTPException(404, "产物不存在")
    outs = json.loads(job.get("extra") or "[]") or ([job["out_path"]] if job["out_path"] else [])
    if index >= len(outs):
        raise HTTPException(404, "产物不存在")
    p = Path(outs[index])
    if not p.exists():
        raise HTTPException(404, "文件已被移动或删除")
    return FileResponse(str(p), filename=p.name)


@app.delete("/api/toolbox/jobs/{jid}")
def toolbox_delete(jid: int, keep_files: bool = True, user: dict = Depends(current_user)):
    job = database.get_tool_job(jid)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404, "任务不存在")
    database.delete_tool_job(jid)
    if not keep_files:
        for p in json.loads(job.get("extra") or "[]") or [job.get("out_path") or ""]:
            if p and Path(p).exists():
                Path(p).unlink()
    return {"ok": True}


# ---------------- 通知中心 ----------------

@app.get("/api/notifications")
def notifications(limit: int = 50, user: dict = Depends(current_user)):
    return {"items": database.list_notifications(user["id"], limit),
            "unread": database.unread_count(user["id"])}


@app.post("/api/notifications/read")
async def notifications_read(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    ids = body.get("ids")
    n = database.mark_notifications_read(
        user["id"], [int(x) for x in ids] if isinstance(ids, list) else None)
    return {"ok": True, "marked": n}


# ---------------- Windows 客户端分发 ----------------

def _app_exe_path() -> Path:
    """客户端 exe 位置：环境变量 > 配置项 > 配置目录/appdist/PacDown.exe。"""
    p = os.environ.get("PACDOWN_APP_EXE") or config.get("app_exe_path") or ""
    return Path(p) if p else config.CONFIG_DIR / "appdist" / "PacDown.exe"


@app.get("/api/app/status")
def app_status(user: dict = Depends(current_user)):
    p = _app_exe_path()
    if p.exists():
        st = p.stat()
        return {"available": True, "name": p.name, "size": st.st_size,
                "updated_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}
    return {"available": False}


@app.get("/api/app/download")
def app_download(user: dict = Depends(current_user)):
    p = _app_exe_path()
    if not p.exists():
        raise HTTPException(404, "客户端文件未上传")
    return FileResponse(str(p), filename=p.name,
                        media_type="application/octet-stream")


# ---------------- 备份 / 恢复（admin） ----------------

@app.get("/api/backup/download")
def backup_download(user: dict = Depends(admin_only)):
    """打包 config.json + metadata.db + 版本信息，供下载保存。"""
    import sqlite3 as _sqlite3
    tmp_dir = database.DB_PATH.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(tmp_dir))
    os.close(fd)
    try:
        # sqlite backup API 做一致性快照
        snap = Path(str(tmp) + ".db")
        src = _sqlite3.connect(str(database.DB_PATH))
        dst = _sqlite3.connect(str(snap))
        with dst:
            src.backup(dst)
        dst.close(); src.close()
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, "metadata.db")
            if config.CONFIG_PATH.exists():
                zf.write(config.CONFIG_PATH, "config.json")
            zf.writestr("version.txt", f"PacDown v1.3 备份\n时间: {datetime.now()}")
        _silent_unlink(str(snap))
    except Exception:
        _silent_unlink(tmp)
        raise HTTPException(500, "备份打包失败")
    return FileResponse(tmp, filename="pacdown-backup.zip",
                        media_type="application/zip",
                        background=BackgroundTask(_silent_unlink, tmp))


@app.post("/api/backup/restore")
async def backup_restore(file: UploadFile, user: dict = Depends(admin_only)):
    """上传备份 zip 恢复数据库与配置（解压到临时文件→backup 覆盖，立即生效）。"""
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(400, "备份文件过大")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        Path(tmp).write_bytes(data)
        tmp_dir = database.DB_PATH.parent / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp) as zf:
            names = zf.namelist()
            if "metadata.db" not in names:
                raise HTTPException(400, "备份文件缺少 metadata.db")
            # 解压到独立临时文件（绝不能直接覆盖在用的 metadata.db）
            tmp_db = tmp_dir / f"restore-{os.getpid()}-{os.urandom(4).hex()}.db"
            with zf.open("metadata.db") as fsrc, open(tmp_db, "wb") as fdst:
                fdst.write(fsrc.read())
            database.restore_from_backup(str(tmp_db))
            tmp_db.unlink(missing_ok=True)
            if "config.json" in names:
                with zf.open("config.json") as fsrc:
                    cfg_text = fsrc.read().decode("utf-8")
                config.CONFIG_PATH.write_text(cfg_text, encoding="utf-8")
                config.load()  # 立即重载配置
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"恢复失败：{str(e)[:150]}")
    finally:
        _silent_unlink(tmp)
    return {"ok": True, "message": "已恢复，立即生效（无需重启）"}


# ---------------- 管理面板：统计（仅 admin 可见） ----------------

@app.get("/api/stats/visits")
def stats_visits(request: Request, days: int = 30, user: dict = Depends(admin_only)):
    return database.visit_stats(min(max(days, 1), 90))


@app.get("/api/stats/downloads")
def stats_downloads(request: Request, days: int = 30, user: dict = Depends(admin_only)):
    days = min(max(days, 1), 90)
    by_day = database.query(
        "SELECT date(downloaded_at) AS d, COUNT(*) AS n, COALESCE(SUM(file_size),0) AS size "
        "FROM videos WHERE status='done' AND downloaded_at!='' AND downloaded_at > "
        f"datetime('now','localtime','-{days} days') GROUP BY d ORDER BY d")
    status_counts = {r["status"]: r["n"] for r in database.query(
        "SELECT status, COUNT(*) AS n FROM videos GROUP BY status")}
    done_total = status_counts.get("done", 0)
    failed_total = status_counts.get("failed", 0)
    top_authors = [{"k": r["author"], "n": r["n"], "size": r["size"]} for r in database.query(
        "SELECT author, COUNT(*) AS n, COALESCE(SUM(file_size),0) AS size FROM videos "
        "WHERE status='done' AND author!='' GROUP BY author ORDER BY n DESC LIMIT 8")]
    # 标签统计（JSON 列在 Python 侧聚合）
    from collections import Counter
    tag_counter: Counter = Counter()
    for r in database.query("SELECT tags FROM videos WHERE tags NOT IN ('','[]')"):
        try:
            for t in json.loads(r["tags"] or "[]"):
                if str(t).strip():
                    tag_counter[str(t).strip()] += 1
        except (ValueError, TypeError):
            continue
    top_tags = [{"k": t, "n": n} for t, n in tag_counter.most_common(8)]
    top_subs = database.query(
        "SELECT platform, uploader_name, new_count, last_checked FROM subscriptions "
        "ORDER BY new_count DESC LIMIT 8")
    tool_usage = database.query(
        "SELECT kind AS k, COUNT(*) AS n FROM tool_jobs GROUP BY kind ORDER BY n DESC LIMIT 8")
    recent_failed = database.query(
        "SELECT id, title, error, created_at FROM videos WHERE status='failed' "
        "ORDER BY id DESC LIMIT 10")
    return {
        "by_day": by_day,
        "by_platform": database.stats()["by_platform"],
        "status_counts": status_counts,
        "success_rate": round(done_total / (done_total + failed_total) * 100, 1)
        if (done_total + failed_total) else 100.0,
        "top_authors": top_authors, "top_tags": top_tags,
        "top_subs": top_subs, "tool_usage": tool_usage,
        "recent_failed": recent_failed,
    }


# ---------------- 自动后处理规则 ----------------

RULE_MATCH_TYPES = ("all", "platform", "subscription", "tag")
RULE_ACTION_KINDS = ("mp3", "transcode", "compress", "gif")


@app.get("/api/rules")
def list_rules(user: dict = Depends(current_user)):
    return {"items": database.list_rules(user["id"])}


@app.post("/api/rules")
async def create_rule(req: Request, user: dict = Depends(current_user)):
    body = await req.json()
    match_type = body.get("match_type") or "all"
    if match_type not in RULE_MATCH_TYPES:
        raise HTTPException(400, "match_type 仅支持 all/platform/subscription/tag")
    if match_type != "all" and not (body.get("match_value") or "").strip():
        raise HTTPException(400, "请填写匹配值")
    actions = body.get("actions") or []
    clean = []
    for a in actions[:6]:
        if not isinstance(a, dict) or a.get("kind") not in RULE_ACTION_KINDS:
            continue
        clean.append({"kind": a["kind"], "params": a.get("params") or {}})
    if not clean:
        raise HTTPException(400, "至少选择一个动作（MP3/转码/压缩/GIF）")
    rid = database.insert_rule({
        "name": (body.get("name") or "").strip()[:60] or "未命名规则",
        "match_type": match_type,
        "match_value": (body.get("match_value") or "").strip()[:120],
        "actions": json.dumps(clean, ensure_ascii=False),
        "enabled": 1,
    }, user_id=user["id"])
    return {"ok": True, "id": rid}


def _own_rule(rid: int, user: dict) -> dict:
    r = database.get_rule(rid)
    if not r:
        raise HTTPException(404, "规则不存在")
    if r.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作该规则")
    return r


@app.patch("/api/rules/{rid}")
async def patch_rule(rid: int, req: Request, user: dict = Depends(current_user)):
    _own_rule(rid, user)
    body = await req.json()
    fields = {}
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
    if "name" in body:
        fields["name"] = (body.get("name") or "").strip()[:60]
    if fields:
        database.update_rule(rid, **fields)
    return {"ok": True}


@app.delete("/api/rules/{rid}")
def del_rule(rid: int, user: dict = Depends(current_user)):
    _own_rule(rid, user)
    database.delete_rule(rid)
    return {"ok": True}


# ---------------- 搬运工作台 ----------------

@app.get("/api/repost/status")
def repost_status(user: dict = Depends(current_user)):
    from . import ai
    return {"ai_ready": ai.ai_ready(),
            "model": config.get("ai_model") or ai.DEFAULT_MODEL}


@app.get("/api/repost/videos")
def repost_videos(keyword: str = "", limit: int = 50, user: dict = Depends(current_user)):
    rows, _ = database.history(status="done", keyword=keyword, page=1, size=limit,
                               user_id=user["id"])
    return {"items": [{"id": r["id"], "title": r["title"], "author": r["author"],
                       "platform": r["platform"], "file_path": r["file_path"],
                       "description": r.get("description") or "",
                       "cover_url": r["cover_url"]} for r in rows]}


@app.post("/api/repost/generate")
async def repost_generate(req: Request, user: dict = Depends(current_user)):
    from . import ai
    body = await req.json()
    vid = body.get("video_id")
    v = _own_video(vid, user) if vid else None
    if not v:
        raise HTTPException(404, "视频不存在")
    try:
        result = ai.rewrite_copy(v, style=body.get("style") or "natural",
                                 credit=bool(body.get("credit", True)))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI 生成失败：{str(e)[:150]}")
    rid = database.insert_repost({
        "video_id": vid, "style": body.get("style") or "natural",
        "credit": 1 if body.get("credit", True) else 0,
        "new_title": result["title"], "new_desc": result["description"],
        "tags": json.dumps(result["tags"], ensure_ascii=False),
    }, user_id=user["id"])
    return {"id": rid, "video_id": vid, **result}


@app.get("/api/repost/list")
def repost_list(video_id: int | None = None, user: dict = Depends(current_user)):
    rows = database.list_reposts(video_id, user["id"])
    for r in rows:
        r["tags"] = json.loads(r.get("tags") or "[]")
    return {"items": rows}


@app.post("/api/repost/{rid}/save")
async def repost_save(rid: int, req: Request, user: dict = Depends(current_user)):
    """用户手动编辑后的文案保存回历史记录。"""
    body = await req.json()
    row = database.query_one("SELECT * FROM reposts WHERE id=? AND user_id=?",
                             (rid, user["id"]))
    if not row:
        raise HTTPException(404, "记录不存在")
    database.execute(
        "UPDATE reposts SET new_title=?, new_desc=?, tags=? WHERE id=?",
        (body.get("title") or "", body.get("description") or "",
         json.dumps(body.get("tags") or []), rid))
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
