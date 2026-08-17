"""FastAPI 应用：全部路由 + 生命周期管理。"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, database, postprocess, scheduler
from .downloader import manager
from .parsers import PLATFORM_META, extract_urls
from .parsers.http_download import REFERERS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="PacDown", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup():
    config.load()
    database.init()
    manager.start()
    scheduler.sub_scheduler.start()


@app.on_event("shutdown")
def _shutdown():
    manager.stop()


# ---------------- 基础 ----------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/platforms")
def platforms():
    return {"platforms": PLATFORM_META,
            "ffmpeg": postprocess.ffmpeg_available()}


# ---------------- 解析 / 下载 ----------------

@app.post("/api/parse")
async def parse(req: Request):
    body = await req.json()
    urls = extract_urls(body.get("text") or "")
    results = []
    for u in urls[:30]:
        try:
            results.append({"url": u, "ok": True,
                            "info": manager.parse_preview(u)})
        except Exception as e:
            results.append({"url": u, "ok": False, "error": str(e)[:200]})
    return {"results": results}


@app.post("/api/download")
async def download(req: Request):
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
    force = bool(body.get("force", False))

    results = []
    for u in urls[:50]:
        try:
            r = manager.create_task(u, options, force=force)
            results.append({"url": u, "status": "queued", "id": r["id"]})
        except Exception as e:
            results.append({"url": u, "status": "failed", "error": str(e)[:200]})
    return {"results": results}


@app.get("/api/tasks")
def tasks():
    return {"tasks": [_task_view(t) for t in database.active_tasks()]}


@app.post("/api/tasks/{vid}/retry")
def retry_task(vid: int):
    if not manager.retry(vid):
        raise HTTPException(400, "仅失败的任务可重试")
    return {"ok": True}


@app.delete("/api/tasks/{vid}")
def dismiss_task(vid: int):
    """清理 duplicate 提示记录（前端展示后调用）。"""
    v = database.get_video(vid)
    if not v:
        raise HTTPException(404, "任务不存在")
    if v["status"] not in ("duplicate", "failed"):
        raise HTTPException(400, "仅提示类任务可移除")
    database.delete_video(vid)
    return {"ok": True}


def _task_view(t: dict) -> dict:
    return {k: t[k] for k in (
        "id", "platform", "title", "author", "cover_url", "status",
        "progress", "speed", "error", "file_path", "duration",
        "created_at", "downloaded_at")}


# ---------------- 历史 ----------------

@app.get("/api/history")
def history(platform: str = "", status: str = "", keyword: str = "",
            page: int = 1, size: int = 24):
    rows, total = database.history(platform, status, keyword, page, size)
    return {"items": [_row_public(r) for r in rows], "total": total,
            "page": page, "size": size}


@app.get("/api/history/groups")
def history_groups(platform: str = "", status: str = "", keyword: str = "",
                   group_by: str = "date"):
    if group_by not in ("date", "platform", "author"):
        raise HTTPException(400, "group_by 仅支持 date/platform/author")
    groups = database.history_groups(platform, status, keyword, group_by)
    return {"groups": [
        {"key": g["key"], "label": g["label"], "count": g["count"],
         "size": g["size"], "items": [_row_public(r) for r in g["items"]]}
        for g in groups
    ]}


@app.get("/api/history/stats")
def history_stats():
    return database.stats()


@app.get("/api/history/export")
def export_csv():
    rows, _ = database.history(size=100000, page=1)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "平台", "标题", "作者", "时长(秒)", "发布时间",
                     "清晰度", "文件路径", "大小(字节)", "下载时间", "状态"])
    for r in rows:
        writer.writerow([r["id"], r["platform"], r["title"], r["author"],
                         r["duration"], r["publish_time"], r["quality"],
                         r["file_path"], r["file_size"], r["downloaded_at"],
                         r["status"]])
    data = buf.getvalue().encode("utf-8-sig")
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=history.csv"})


@app.get("/api/history/{vid}")
def history_detail(vid: int):
    v = database.get_video(vid)
    if not v:
        raise HTTPException(404, "记录不存在")
    return _row_public(v, full=True)


@app.delete("/api/history/{vid}")
def delete_history(vid: int, keep_files: bool = True):
    v = database.delete_video(vid)
    if not v:
        raise HTTPException(404, "记录不存在")
    removed = []
    if not keep_files:
        for key in ("file_path", "cover_path", "audio_path", "danmaku_path"):
            p = v.get(key) or ""
            if p and Path(p).exists():
                Path(p).unlink()
                removed.append(Path(p).name)
        for img in json.loads(v.get("images") or "[]"):
            if Path(img).exists():
                Path(img).unlink()
    return {"ok": True, "files_removed": removed}


@app.post("/api/history/{vid}/open")
def open_folder(vid: int):
    v = database.get_video(vid)
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


def _row_public(r: dict, full: bool = False) -> dict:
    keys = ["id", "platform", "video_id", "title", "description", "author",
            "cover_url", "cover_path", "duration", "publish_time", "quality",
            "file_path", "file_size", "audio_path", "danmaku_path", "images",
            "status", "progress", "error", "created_at", "downloaded_at"]
    out = {k: r.get(k, "") for k in keys}
    out["stats"] = json.loads(r.get("stats") or "{}")
    try:
        out["comments"] = json.loads(r.get("comments") or "[]")
    except json.JSONDecodeError:
        out["comments"] = []
    if full:
        out["raw"] = json.loads(r.get("raw_json") or "{}")
        out["source_url"] = r.get("source_url", "")
    return out


# ---------------- 本地文件预览（带 Range，支持视频拖动进度条） ----------------

MIME = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


@app.get("/api/file")
def serve_file(request: Request, id: int, type: str = "video", index: int = 0):
    """按记录 ID 输出已下载文件。路径取自数据库而非用户输入，杜绝目录遍历。"""
    v = database.get_video(id)
    if not v:
        raise HTTPException(404, "记录不存在")
    if type == "image":
        images = json.loads(v.get("images") or "[]")
        if index >= len(images):
            raise HTTPException(404, "图片不存在")
        path = Path(images[index])
    elif type == "audio":
        path = Path(v.get("audio_path") or "")
    else:
        path = Path(v.get("file_path") or "")
    if not str(path):
        raise HTTPException(404, "文件不存在")
    if not path.exists():
        raise HTTPException(404, "文件已被移动或删除")

    media_type = MIME.get(path.suffix.lower(), "application/octet-stream")
    size = path.stat().st_size
    range_header = request.headers.get("range")

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
                                     "Accept-Ranges": "bytes",
                                     "Cache-Control": "private, max-age=3600"})
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
                             headers={"Accept-Ranges": "bytes",
                                      "Content-Length": str(size),
                                      "Cache-Control": "private, max-age=3600"})


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
             "xiaohongshu_cookie", "ai_api_key")


@app.get("/api/config")
def get_config():
    cfg = config.all_settings()
    for k in SENSITIVE:
        cfg[k] = "__SET__" if cfg.get(k) else ""
    cfg["ffmpeg"] = postprocess.ffmpeg_available()
    cfg["download_dir"] = config.get("download_dir")
    return cfg


@app.post("/api/config")
async def set_config(req: Request):
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
def get_dirs():
    return {"current": config.get("download_dir"),
            "recent": config.get("recent_dirs", [])}


@app.post("/api/config/dirs")
async def set_dir(req: Request):
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
def list_subs():
    return {"items": database.list_subs()}


@app.post("/api/subscriptions")
async def add_sub(req: Request):
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
    sid = database.insert_sub({
        "platform": platform, "uploader_id": uploader_id,
        "uploader_name": info["name"], "avatar_url": info["avatar"],
        "source_url": url, "enabled": 1,
    })
    if not sid:
        raise HTTPException(409, "该博主已在订阅列表中")
    return {"ok": True, "id": sid}


@app.patch("/api/subscriptions/{sid}")
async def patch_sub(sid: int, req: Request):
    body = await req.json()
    sub = database.get_sub(sid)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    fields = {}
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
    if fields:
        database.update_sub(sid, **fields)
    return {"ok": True}


@app.delete("/api/subscriptions/{sid}")
def del_sub(sid: int):
    database.delete_sub(sid)
    return {"ok": True}


@app.post("/api/subscriptions/{sid}/check")
def check_sub(sid: int):
    sub = database.get_sub(sid)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    try:
        n = scheduler.check_subscription(sub)
    except Exception as e:
        raise HTTPException(502, str(e)[:200])
    return {"ok": True, "new_count": n}


# ---------------- 搬运工作台 ----------------

@app.get("/api/repost/status")
def repost_status():
    from . import ai
    return {"ai_ready": ai.ai_ready(),
            "model": config.get("ai_model") or ai.DEFAULT_MODEL}


@app.get("/api/repost/videos")
def repost_videos(keyword: str = "", limit: int = 50):
    rows, _ = database.history(status="done", keyword=keyword, page=1, size=limit)
    return {"items": [{"id": r["id"], "title": r["title"], "author": r["author"],
                       "platform": r["platform"], "file_path": r["file_path"],
                       "description": r.get("description") or "",
                       "cover_url": r["cover_url"]} for r in rows]}


@app.post("/api/repost/generate")
async def repost_generate(req: Request):
    from . import ai
    body = await req.json()
    vid = body.get("video_id")
    v = database.get_video(vid) if vid else None
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
    })
    return {"id": rid, "video_id": vid, **result}


@app.get("/api/repost/list")
def repost_list(video_id: int | None = None):
    rows = database.list_reposts(video_id)
    for r in rows:
        r["tags"] = json.loads(r.get("tags") or "[]")
    return {"items": rows}


@app.post("/api/repost/{rid}/save")
async def repost_save(rid: int, req: Request):
    """用户手动编辑后的文案保存回历史记录。"""
    body = await req.json()
    row = database.query_one("SELECT * FROM reposts WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "记录不存在")
    database.execute(
        "UPDATE reposts SET new_title=?, new_desc=?, tags=? WHERE id=?",
        (body.get("title") or "", body.get("description") or "",
         json.dumps(body.get("tags") or []), rid))
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
