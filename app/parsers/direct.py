"""直链 / m3u8 下载器：任意 http(s) 资源直链批量下载。

适用场景：网盘直链（配合浏览器直链插件获取真实地址）、静态资源链接、
m3u8(HLS) 流。识别规则：URL 路径后缀命中常见媒体/文件扩展名，或链接带
`#direct` 锚点强制。下载支持断点续传（.part 临时文件 + Range）。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .. import config
from . import http_download as hd
from . import ytdlp_engine
from .base import ParseError, Parser, VideoInfo, register

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".avi", ".ts", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
OTHER_EXTS = {".zip", ".rar", ".7z", ".pdf", ".mpg", ".mpeg"}
KNOWN_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | OTHER_EXTS | {".m3u8"}


def _url_ext(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def _split_anchor(url: str) -> tuple[str, str, str]:
    """解析 `#direct` 锚点：`...#direct;name=文件名.mp4;referer=https://...`。"""
    if "#" not in url:
        return url, "", ""
    base, _, frag = url.partition("#")
    if not frag.lower().startswith("direct"):
        return url, "", ""
    name, referer = "", ""
    for part in frag.split(";")[1:]:
        k, _, v = part.partition("=")
        if k.lower() == "name" and v:
            name = v
        elif k.lower() == "referer" and v:
            referer = v
    return base, unquote(name), unquote(referer)


@register
class DirectParser(Parser):
    platform = "direct"
    display_name = "直链"

    def can_parse(self, url: str) -> bool:
        low = url.lower()
        if "#direct" in low:
            return True
        return _url_ext(url) in KNOWN_EXTS

    # ---------- 解析 ----------

    def parse(self, url: str) -> VideoInfo:
        real_url, anchor_name, anchor_referer = _split_anchor(url)
        ext = _url_ext(real_url)
        if ext == ".m3u8":
            return self._parse_m3u8(real_url, anchor_name, anchor_referer)

        name = anchor_name
        size, ctype, disposition = 0, "", ""
        try:
            with hd.client("", mobile=False) as c:
                try:
                    r = c.head(real_url)
                    if r.status_code >= 400 or not r.headers.get("content-type"):
                        raise ValueError("HEAD 不可用")
                    headers = r.headers
                except Exception:
                    # 流式 GET：只读响应头，不下载 body
                    with c.stream("GET", real_url,
                                  headers={"Range": "bytes=0-0"}) as r:
                        headers = r.headers
                size = int(headers.get("content-length") or 0)
                if "content-range" in headers:  # 0-0 探测时总长从 content-range 取
                    m = re.search(r"/(\d+)$", headers["content-range"])
                    if m:
                        size = int(m.group(1))
                ctype = (headers.get("content-type") or "").lower()
                disposition = headers.get("content-disposition") or ""
        except Exception:
            if not anchor_name:
                raise ParseError("直链无法访问（可能已失效或需要登录/特定 Referer）。"
                                 "若确认链接有效，可在链接后加 #direct;name=文件名.mp4 重试")

        if not name:
            name = _name_from_disposition(disposition) or _name_from_url(real_url)
        if not ext and ctype:
            ext = _ext_from_mime(ctype)
        kind = _kind_of(ext, ctype)
        return VideoInfo(
            platform=self.platform,
            video_id=hashlib.md5(real_url.encode()).hexdigest()[:16],
            source_url=url,
            title=name,
            author="直链下载",
            duration=0,
            stats={"size": size} if size else {},
            quality_options=[{"id": "best", "label": "原始文件", "height": 0}],
            raw={"url": real_url, "ext": ext, "kind": kind, "size": size,
                 "referer": anchor_referer, "content_type": ctype},
        )

    def _parse_m3u8(self, url: str, anchor_name: str, referer: str) -> VideoInfo:
        name = anchor_name or _name_from_url(url) or "m3u8 流媒体"
        if name.endswith(".m3u8"):
            name = name[:-5]
        return VideoInfo(
            platform=self.platform,
            video_id=hashlib.md5(url.encode()).hexdigest()[:16],
            source_url=url,
            title=name,
            author="m3u8 流",
            quality_options=[{"id": "best", "label": "原画", "height": 0}],
            raw={"url": url, "ext": ".mp4", "kind": "video",
                 "is_m3u8": True, "referer": referer},
        )

    # ---------- 下载 ----------

    def download(self, info, dest_dir: str, options: dict, progress,
                 filename_prefix: str = "") -> dict:
        raw = info.raw or {}
        url = raw.get("url") or info.source_url
        referer = raw.get("referer") or ""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        name = filename_prefix or hd.safe_filename(info.title or "direct")

        if raw.get("is_m3u8"):
            opts = dict(options or {})
            if referer:
                opts["_referer"] = referer
            return ytdlp_engine.download(url, str(dest), "direct", opts,
                                         progress, filename_prefix=name)

        ext = raw.get("ext") or ".bin"
        if not ext.startswith("."):
            ext = "." + ext
        if name.lower().endswith(ext.lower()):  # 模板/标题已带扩展名时不重复
            name = name[: -len(ext)]
        final = dest / f"{name}{ext}"
        result = hd.stream_download(
            url, final, "", progress, referer=referer, resume=True)
        if raw.get("kind") == "image":
            result["images"] = [result["file_path"]]
        return result


def _name_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name
    return name or ""


def _name_from_disposition(disposition: str) -> str:
    if not disposition:
        return ""
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.I)
    if m:
        return unquote(m.group(1)).strip().strip('"')
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _ext_from_mime(ctype: str) -> str:
    table = {
        "video/mp4": ".mp4", "video/webm": ".webm",
        "video/x-matroska": ".mkv", "video/quicktime": ".mov",
        "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav",
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "application/zip": ".zip",
        "application/pdf": ".pdf",
    }
    return table.get(ctype.split(";")[0].strip(), "")


def _kind_of(ext: str, ctype: str) -> str:
    if ext in VIDEO_EXTS or ctype.startswith("video/"):
        return "video"
    if ext in AUDIO_EXTS or ctype.startswith("audio/"):
        return "audio"
    if ext in IMAGE_EXTS or ctype.startswith("image/"):
        return "image"
    return "file"
