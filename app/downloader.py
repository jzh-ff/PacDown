"""下载管理器：任务入库 → worker 线程池并发下载 → 后处理 → 元数据落盘。

一条 videos 记录即一个任务（含实时进度）；worker 轮询 pending 任务执行。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from . import config, database, postprocess
from .parsers import dispatch, ParseError
from .parsers.http_download import REFERERS, safe_filename


class DownloadManager:
    def __init__(self):
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = threading.Event()

    # ---------- 对外接口 ----------

    def parse_preview(self, url: str) -> dict:
        """仅解析（供前端预览），不创建任务。"""
        parser = dispatch(url)
        info = parser.parse(url)
        return self._info_to_dict(parser, info)

    def create_task(self, url: str, options: dict, force: bool = False) -> dict:
        """立即入库（parsing 状态），解析由 worker 后台完成——点击下载零等待。"""
        from .parsers import guess_platform
        options = dict(options)
        options["_force"] = bool(force)
        vid = database.insert_video({
            "platform": guess_platform(url) or "generic",
            "source_url": url,
            "status": "parsing",
            "options": json.dumps(options, ensure_ascii=False),
        })
        self._wake.set()
        return {"duplicate": False, "id": vid}

    def retry(self, vid: int) -> bool:
        v = database.get_video(vid)
        if v and v["status"] == "failed":
            # 解析阶段失败的回到 parsing；下载阶段失败的直接重下
            new_status = "parsing" if not v.get("video_id") else "pending"
            database.update_video(vid, status=new_status, progress=0, error="", speed="")
            self._wake.set()
            return True
        return False

    def start(self) -> None:
        database.init()
        n = max(1, int(config.get("max_concurrency", 3)))
        for i in range(n):
            t = threading.Thread(target=self._worker, name=f"dl-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # ---------- 内部 ----------

    def _worker(self):
        while not self._stop.is_set():
            # 优先处理待解析任务（parsing → pending）
            task = database.query_one(
                "SELECT * FROM videos WHERE status='parsing' ORDER BY id LIMIT 1")
            if task:
                try:
                    task = self._parse_stage(task)
                except Exception as e:
                    database.update_video(task["id"], status="failed",
                                          error=str(e)[:500], speed="")
                    task = None
                if task:
                    try:
                        self._run(task)
                    except Exception as e:
                        database.update_video(task["id"], status="failed",
                                              error=str(e)[:500], speed="")
                continue
            # 再取已就绪的下载任务
            task = database.query_one(
                "SELECT * FROM videos WHERE status='pending' ORDER BY id LIMIT 1")
            if not task:
                self._wake.wait(timeout=1.5)
                self._wake.clear()
                continue
            try:
                self._run(task)
            except Exception as e:  # 兜底：任何异常都标记失败
                database.update_video(task["id"], status="failed",
                                      error=str(e)[:500], speed="")
            # 给其他 worker 抢任务的机会
            time.sleep(0.1)

    def _parse_stage(self, task: dict) -> dict | None:
        """后台解析：拉取元数据、去重检查，成功则更新记录并返回（状态 pending）。"""
        vid = task["id"]
        options = json.loads(task["options"] or "{}")
        force = bool(options.pop("_force", False))
        database.update_video(vid, options=json.dumps(options, ensure_ascii=False))

        parser = dispatch(task["source_url"])
        info = parser.parse(task["source_url"])
        info.raw["_images"] = info.images
        info.raw["_is_images"] = info.is_images

        if not force and info.video_id:
            existing = database.find_by_video_id(info.platform, info.video_id)
            if existing and existing["id"] != vid and existing["status"] in (
                    "done", "downloading", "pending", "processing"):
                database.update_video(
                    vid, status="duplicate", platform=info.platform,
                    video_id=info.video_id, title=info.title, author=info.author,
                    cover_url=info.cover_url,
                    error=f"已于 {existing['downloaded_at'][:16]} 下载过（历史记录 #{existing['id']}）")
                return None

        database.update_video(
            vid,
            platform=info.platform, video_id=info.video_id, title=info.title,
            description=info.description, author=info.author,
            author_id=info.author_id, avatar_url=info.avatar_url,
            cover_url=info.cover_url, duration=info.duration,
            publish_time=info.publish_time,
            stats=json.dumps(info.stats, ensure_ascii=False),
            quality=options.get("quality") or config.get("default_quality", "best"),
            raw_json=json.dumps(info.raw, ensure_ascii=False, default=str),
            status="pending", progress=0)
        return database.get_video(vid)

    def _run(self, task: dict):
        vid = task["id"]
        options = json.loads(task["options"] or "{}")
        parser = dispatch(task["source_url"])
        database.update_video(vid, status="downloading", progress=0, speed="", error="")

        # 目标目录：下载目录/平台/作者/
        author = safe_filename(task["author"] or "未知作者", 40)
        dest_dir = Path(config.get("download_dir")) / task["platform"] / author
        date = (task["publish_time"] or "")[:10].replace("-", "") or datetime.now().strftime("%Y%m%d")
        prefix = safe_filename(f"{date}_{task['title'] or task['video_id']}", 90)

        last = {"t": 0.0}

        def progress(pct, speed):
            now = time.time()
            if now - last["t"] > 0.4 or pct >= 100:
                last["t"] = now
                database.update_video(vid, progress=round(pct, 1), speed=str(speed)[:30])

        result = parser.download(
            _rebuild_info(task), str(dest_dir), options, progress,
            filename_prefix=prefix if task["platform"] in ("bilibili", "generic") else "",
        )
        database.update_video(vid, progress=100, speed="后处理中", status="processing")

        file_path = result["file_path"]
        stem = Path(file_path).stem
        same_dir = Path(file_path).parent
        fields: dict = {
            "file_path": file_path,
            "file_size": result.get("file_size", 0),
            "images": json.dumps(result.get("images", []), ensure_ascii=False),
        }

        # 封面
        cover_path = ""
        if task["cover_url"]:
            try:
                cover_path = str((same_dir / f"{stem}_cover.jpg").resolve())
                self._download_cover(task["cover_url"], task["platform"], cover_path)
                fields["cover_path"] = cover_path
            except Exception:
                pass

        # B站弹幕
        if task["platform"] == "bilibili" and options.get("download_danmaku"):
            try:
                cid = parser.get_cid(task["video_id"])
                if cid:
                    fields["danmaku_path"] = postprocess.download_danmaku(
                        cid, str(same_dir / f"{stem}.xml"))
            except Exception as e:
                fields["danmaku_path"] = ""
                database.update_video(vid, error=f"弹幕失败：{str(e)[:100]}")

        # 评论抓取：B站热评（av 号）/ 抖音热评（ies v2 免登录接口）
        if options.get("fetch_comments"):
            try:
                if task["platform"] == "bilibili":
                    aid = self._bilibili_aid(parser, task)
                    if aid:
                        fields["comments"] = json.dumps(
                            postprocess.fetch_bilibili_comments(aid), ensure_ascii=False)
                elif task["platform"] == "douyin" and task.get("video_id"):
                    fields["comments"] = json.dumps(
                        postprocess.fetch_douyin_comments(task["video_id"]),
                        ensure_ascii=False)
            except Exception as e:
                database.update_video(
                    vid, error=(task["error"] or "") + f"评论失败：{str(e)[:100]}")

        # MP3 提取
        if options.get("extract_audio"):
            try:
                fields["audio_path"] = postprocess.extract_mp3(
                    file_path, str(same_dir / f"{stem}.mp3"))
            except Exception as e:
                database.update_video(vid, error=(task["error"] or "") + f"音频失败：{str(e)[:100]}")

        # sidecar JSON
        try:
            sidecar = self._build_sidecar(task, fields)
            (same_dir / f"{stem}.json").write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        fields.update({"status": "done", "downloaded_at": database.now(), "speed": ""})
        database.update_video(vid, **fields)

    # ---------- 工具 ----------

    def _bilibili_aid(self, parser, task: dict) -> int | None:
        """取B站 av 数字号（评论接口需要 aid 而非 BV 号）。"""
        try:
            view = parser._web_view(task["video_id"])
            return int(view.get("aid") or 0) or None
        except Exception:
            return None

    def _download_cover(self, url: str, platform: str, dest: str):
        referer = REFERERS.get(platform, "")
        headers = {"User-Agent": "Mozilla/5.0", **({"Referer": referer} if referer else {})}
        with httpx.Client(timeout=15, follow_redirects=True,
                          proxy=config.get("http_proxy") or None) as c:
            r = c.get(url, headers=headers)
            r.raise_for_status()
        Path(dest).write_bytes(r.content)

    def _build_sidecar(self, task: dict, fields: dict) -> dict:
        return {
            "platform": task["platform"],
            "video_id": task["video_id"],
            "source_url": task["source_url"],
            "title": task["title"],
            "description": task.get("description") or "",
            "author": task["author"],
            "author_id": task["author_id"],
            "duration": task["duration"],
            "publish_time": task["publish_time"],
            "stats": json.loads(task["stats"] or "{}"),
            "quality": task["quality"],
            "file_path": fields.get("file_path"),
            "file_size": fields.get("file_size"),
            "cover": task["cover_url"],
            "audio_path": fields.get("audio_path", ""),
            "danmaku_path": fields.get("danmaku_path", ""),
            "images": fields.get("images", "[]"),
            "comments": json.loads(fields.get("comments") or "[]"),
            "downloaded_at": database.now(),
            "raw": json.loads(task["raw_json"] or "{}"),
        }

    def _info_to_dict(self, parser, info) -> dict:
        return {
            "platform": info.platform,
            "platform_name": getattr(parser, "display_name", info.platform),
            "video_id": info.video_id,
            "source_url": info.source_url,
            "title": info.title,
            "author": info.author,
            "author_id": info.author_id,
            "cover_url": info.cover_url,
            "duration": info.duration,
            "publish_time": info.publish_time,
            "stats": info.stats,
            "quality_options": info.quality_options,
            "is_images": info.is_images,
            "image_count": len(info.images),
        }


def _rebuild_info(task: dict):
    """从库里的记录重建 VideoInfo（供 parser.download 使用）。"""
    from .parsers.base import VideoInfo
    raw = json.loads(task["raw_json"] or "{}")
    images = raw.get("_images") or []
    is_images = bool(images) and raw.get("_is_images", False) or _looks_like_images(raw)
    return VideoInfo(
        platform=task["platform"], video_id=task["video_id"],
        source_url=task["source_url"], title=task["title"],
        author=task["author"], cover_url=task["cover_url"],
        images=images, is_images=bool(is_images),
        raw=raw,
    )


def _looks_like_images(raw: dict) -> bool:
    return bool(raw.get("aweme", {}).get("images")) if isinstance(raw.get("aweme"), dict) else False


manager = DownloadManager()
