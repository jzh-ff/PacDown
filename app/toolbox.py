"""工具箱：视频（ffmpeg）与图片（Pillow）后处理任务。

任务落 tool_jobs 表，单个后台线程串行执行（ffmpeg 属重负载，避免抢占下载带宽）。
ffmpeg 用 `-progress pipe:1` 解析实时进度；图片工具按处理张数推进。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from . import config, database, postprocess

# 工具清单（kind → 中文名）；前端按此渲染工具卡片
TOOLS = {
    "mp3": "提取 MP3",
    "transcode": "转码",
    "compress": "压缩",
    "trim": "剪辑",
    "gif": "GIF 动图",
    "watermark": "文字水印",
    "frame": "截帧",
    "img_convert": "图片转换/压缩",
    "img_join": "拼接长图",
    "img_zip": "图集打包 ZIP",
}

VIDEO_TOOLS = {"mp3", "transcode", "compress", "trim", "gif", "watermark", "frame"}
IMAGE_TOOLS = {"img_convert", "img_join", "img_zip"}

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def uploads_dir() -> Path:
    d = database.DB_PATH.parent / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_dir() -> Path:
    d = Path(config.get("download_dir")) / "_tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(filename: str, data: bytes) -> dict:
    """保存上传文件，返回 {name, size, media}。文件名做安全化处理。"""
    from .parsers.http_download import safe_filename
    ext = Path(filename).suffix.lower()
    if ext not in _IMG_EXTS | {".mp4", ".mkv", ".mov", ".webm", ".flv", ".mp3", ".m4a", ".wav"}:
        raise ValueError(f"不支持的文件类型：{ext or '未知'}")
    stem = safe_filename(Path(filename).stem, 60) or "upload"
    name = f"{stem}{ext}"
    dest = uploads_dir() / name
    i = 1
    while dest.exists():  # 同名冲突自动加序号
        dest = uploads_dir() / f"{stem}_{i}{ext}"
        i += 1
    dest.write_bytes(data)
    media = "image" if ext in _IMG_EXTS else ("video" if ext != ".mp3" else "audio")
    return {"name": dest.name, "size": len(data), "media": media}


# ---------------- 素材解析 ----------------

def resolve_video(video_id: int = 0, upload: str = "") -> tuple[Path, str]:
    """解析视频源：片库记录或上传文件。返回 (路径, 展示名)。"""
    if video_id:
        v = database.get_video(video_id)
        if not v or not v.get("file_path"):
            raise ValueError("该记录没有视频文件")
        p = Path(v["file_path"])
        if not p.exists():
            raise ValueError("文件已被移动或删除")
        return p, v.get("title") or p.stem
    if upload:
        p = uploads_dir() / Path(upload).name  # 防目录遍历
        if not p.exists():
            raise ValueError("上传文件不存在")
        return p, p.stem
    raise ValueError("请先选择素材")


def resolve_images(video_id: int = 0, upload: str = "") -> tuple[list[Path], str]:
    """解析图片源：图集记录的全部图片，或单个上传图片。"""
    if video_id:
        v = database.get_video(video_id)
        imgs = [Path(x) for x in json.loads(v.get("images") or "[]")]
        imgs = [p for p in imgs if p.exists()]
        if not imgs and v and v.get("cover_path") and Path(v["cover_path"]).exists():
            imgs = [Path(v["cover_path"])]
        if not imgs:
            raise ValueError("该记录没有图片（仅图集/封面可用）")
        return imgs, (v.get("title") or "images")
    if upload:
        p = uploads_dir() / Path(upload).name
        if not p.exists():
            raise ValueError("上传文件不存在")
        return [p], p.stem
    raise ValueError("请先选择素材")


# ---------------- ffmpeg 封装 ----------------

def _run_ffmpeg(args: list[str], duration: float, progress) -> None:
    """执行 ffmpeg 并按 out_time 汇报进度。args 不含 ffmpeg 前缀与 -i 之外的通用项。"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1", *args]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=_NO_WINDOW)
    assert p.stdout is not None
    for line in p.stdout:
        line = line.strip()
        if line.startswith("out_time_us=") and duration > 0:
            try:
                us = int(line.split("=", 1)[1])
                progress(min(us / 1e6 / duration * 100, 99.0))
            except ValueError:
                pass
    p.wait()
    if p.returncode != 0:
        err = p.stderr.read() if p.stderr else ""
        raise RuntimeError(f"ffmpeg 失败：{err[:300]}")


def _font_file() -> str:
    """drawtext 用的本机中文字体。"""
    candidates = [
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for f in candidates:
        if Path(f).exists():
            return f
    return ""


def _esc_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _watermark_pos(pos: str, size: int) -> str:
    margin = 24
    return {
        "tl": f"x={margin}:y={margin}",
        "tr": f"x=w-tw-{margin}:y={margin}",
        "bl": f"x={margin}:y=h-th-{margin}",
        "br": f"x=w-tw-{margin}:y=h-th-{margin}",
        "center": "x=(w-tw)/2:y=(h-th)/2",
    }.get(pos, f"x=w-tw-{margin}:y=h-th-{margin}")


def _do_video_tool(kind: str, src: Path, stem: str, params: dict, progress) -> dict:
    if not postprocess.ffmpeg_available():
        raise RuntimeError("未检测到 ffmpeg。请安装 ffmpeg 并加入 PATH 后重试")
    dur = postprocess.ffprobe_duration(str(src))
    out = out_dir()

    if kind == "mp3":
        dst = out / f"{stem}.mp3"
        progress(10)
        postprocess.extract_mp3(str(src), str(dst))  # 长任务，无细粒度进度
        progress(100)
        return {"out": dst}

    if kind == "transcode":
        vcodec = {"h264": "libx264", "h265": "libx265",
                  "vp9": "libvpx-vp9", "copy": "copy"}.get(params.get("vcodec"), "libx264")
        crf = int(params.get("crf") or 23)
        res = str(params.get("resolution") or "source")
        vf = []
        if res != "source" and res.isdigit():
            vf.append(f"scale=-2:{res}")
        dst = out / f"{stem}_x264.mp4" if vcodec == "libx264" else out / f"{stem}_{params.get('vcodec', 'trans')}.mp4"
        args = ["-i", str(src)]
        if vf:
            args += ["-vf", ",".join(vf)]
        if vcodec == "copy":
            args += ["-c", "copy"]
        else:
            args += ["-c:v", vcodec, "-crf", str(crf), "-preset", "medium",
                     "-c:a", "aac", "-b:a", "128k"]
        args.append(str(dst))
        _run_ffmpeg(args, dur, progress)
        return {"out": dst}

    if kind == "compress":
        crf = int(params.get("crf") or 28)
        preset = str(params.get("preset") or "medium")
        dst = out / f"{stem}_crf{crf}.mp4"
        _run_ffmpeg(["-i", str(src), "-c:v", "libx264", "-crf", str(crf),
                     "-preset", preset, "-c:a", "aac", "-b:a", "96k", str(dst)],
                    dur, progress)
        return {"out": dst}

    if kind == "trim":
        start = str(params.get("start") or "0")
        end = str(params.get("end") or "")
        dst = out / f"{stem}_cut.mp4"
        args = ["-ss", start]
        if end:
            args += ["-to", end]
        args += ["-i", str(src), "-c", "copy", str(dst)]
        progress(30)  # copy 模式极快，无进度回调
        _run_ffmpeg(args, 0, progress)
        return {"out": dst}

    if kind == "gif":
        start = str(params.get("start") or "0")
        length = float(params.get("duration") or 5)
        fps = int(params.get("fps") or 12)
        width = int(params.get("width") or 480)
        dst = out / f"{stem}.gif"
        _run_ffmpeg(
            ["-ss", start, "-t", str(length), "-i", str(src),
             "-vf", f"fps={fps},scale={width}:-1:flags=lanczos",
             "-loop", "0", str(dst)],
            length, progress)
        return {"out": dst}

    if kind == "watermark":
        text = str(params.get("text") or "").strip()
        if not text:
            raise ValueError("水印文字不能为空")
        font = _font_file()
        if not font:
            raise RuntimeError("未找到可用字体，无法绘制文字水印")
        size = int(params.get("fontsize") or 32)
        opacity = float(params.get("opacity") or 0.7)
        pos = _watermark_pos(str(params.get("position") or "br"), size)
        dst = out / f"{stem}_wm.mp4"
        draw = (f"drawtext=fontfile='{font}':text='{_esc_drawtext(text)}':"
                f"fontsize={size}:fontcolor=white@{opacity}:"
                f"shadowcolor=black@0.5:shadowx=1:shadowy=1:{pos}")
        _run_ffmpeg(["-i", str(src), "-vf", draw, "-c:a", "copy", str(dst)],
                    dur, progress)
        return {"out": dst}

    if kind == "frame":
        at = str(params.get("at") or "20%")
        dst = out / f"{stem}_frame.jpg"
        progress(30)
        if at.endswith("%"):
            postprocess.extract_frame(str(src), str(dst), percent=float(at[:-1] or 20) / 100)
        else:
            postprocess.extract_frame(str(src), str(dst), at_sec=float(at or 2))
        return {"out": dst}

    raise ValueError(f"未知视频工具：{kind}")


# ---------------- 图片工具（Pillow） ----------------

def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        raise RuntimeError("图片处理需要 Pillow：pip install Pillow")


def _do_image_tool(kind: str, images: list[Path], stem: str, params: dict, progress) -> dict:
    Image = _pil()
    out = out_dir()

    if kind == "img_convert":
        fmt = str(params.get("format") or "webp").lower()
        ext = {"jpg": ".jpg", "jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt)
        if not ext:
            raise ValueError("format 仅支持 webp/jpg/png")
        quality = min(95, max(10, int(params.get("quality") or 85)))
        max_w = int(params.get("max_width") or 0)
        outs = []
        for i, p in enumerate(images, 1):
            img = Image.open(p)
            if max_w and img.width > max_w:
                img = img.resize((max_w, int(img.height * max_w / img.width)),
                                 Image.LANCZOS)
            dst = out / f"{stem}_{i:02d}{ext}" if len(images) > 1 else out / f"{stem}{ext}"
            if ext == ".jpg":
                img = img.convert("RGB")
            img.save(dst, quality=quality)
            outs.append(str(dst.resolve()))
            progress(i / len(images) * 100)
        return {"out": outs[0], "extra": outs}

    if kind == "img_join":
        max_w = min(4096, int(params.get("max_width") or 1080))
        imgs = []
        for i, p in enumerate(images, 1):
            img = Image.open(p).convert("RGB")
            if img.width > max_w:
                img = img.resize((max_w, int(img.height * max_w / img.width)),
                                 Image.LANCZOS)
            imgs.append(img)
            progress(i / len(images) * 60)
        total_h = sum(im.height for im in imgs)
        if total_h > 30000:
            raise ValueError("拼接后图片过长，请减少图片数量")
        canvas = Image.new("RGB", (max(im.width for im in imgs), total_h), "white")
        y = 0
        for i, im in enumerate(imgs, 1):
            canvas.paste(im, (0, y))
            y += im.height
            progress(60 + i / len(imgs) * 40)
        dst = out / f"{stem}_long.jpg"
        canvas.save(dst, quality=int(params.get("quality") or 88))
        return {"out": dst}

    if kind == "img_zip":
        dst = out / f"{stem}.zip"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, p in enumerate(images, 1):
                zf.write(p, p.name)
                progress(i / len(images) * 100)
        return {"out": dst}

    raise ValueError(f"未知图片工具：{kind}")


# ---------------- 任务执行 ----------------

def run_job(jid: int) -> None:
    job = database.get_tool_job(jid)
    if not job or job["status"] != "pending":
        return
    database.update_tool_job(jid, status="running", progress=0)

    def progress(pct):
        database.update_tool_job(jid, progress=round(float(pct), 1))

    try:
        params = json.loads(job["params"] or "{}")
        src = job["src"]  # video:{id} / upload:{name}
        stype, _, sval = src.partition(":")
        kind = job["kind"]
        if kind in VIDEO_TOOLS:
            path, stem = resolve_video(int(sval) if stype == "video" else 0,
                                       sval if stype == "upload" else "")
            result = _do_video_tool(kind, path, stem, params, progress)
        else:
            images, stem = resolve_images(int(sval) if stype == "video" else 0,
                                          sval if stype == "upload" else "")
            result = _do_image_tool(kind, images, stem, params, progress)
        database.update_tool_job(
            jid, status="done", progress=100, finished_at=database.now(),
            out_path=str(result["out"]),
            extra=json.dumps(result.get("extra") or [], ensure_ascii=False))
    except Exception as e:
        database.update_tool_job(jid, status="failed", error=str(e)[:300],
                                 finished_at=database.now())


class ToolManager:
    """单线程串行执行工具任务。"""

    def __init__(self):
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, name="tool-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def create(self, kind: str, src: str, params: dict) -> int:
        if kind not in TOOLS:
            raise ValueError("未知工具")
        # 入队前先校验素材可用，错误直接抛给前端
        stype, _, sval = src.partition(":")
        if kind in VIDEO_TOOLS:
            resolve_video(int(sval) if stype == "video" else 0,
                          sval if stype == "upload" else "")
        else:
            resolve_images(int(sval) if stype == "video" else 0,
                           sval if stype == "upload" else "")
        jid = database.insert_tool_job({
            "kind": kind, "src": src, "status": "pending",
            "params": json.dumps(params or {}, ensure_ascii=False),
        })
        self._wake.set()
        return jid

    def _worker(self):
        while not self._stop.is_set():
            job = database.query_one(
                "SELECT id FROM tool_jobs WHERE status='pending' ORDER BY id LIMIT 1")
            if not job:
                self._wake.wait(timeout=2)
                self._wake.clear()
                continue
            run_job(job["id"])


tool_manager = ToolManager()
