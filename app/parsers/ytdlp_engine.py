"""yt-dlp 引擎封装：B站与通用解析器共用（探测元数据 / 下载 / 进度回调）。"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from yt_dlp import YoutubeDL

from .. import config
from .base import ParseError


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "nocheckcertificate": True,
    }
    proxy = config.get("http_proxy", "")
    if proxy:
        opts["proxy"] = proxy
    return opts


def _cookie_file(platform: str) -> str | None:
    """把配置里的 cookie 字符串转成 Netscape 格式临时文件供 yt-dlp 使用。"""
    raw = config.get(f"{platform}_cookie", "") or ""
    raw = raw.strip()
    if not raw or "SESSDATA" not in raw and platform == "bilibili":
        return None
    pairs = []
    for part in re.split(r";\s*", raw):
        if "=" in part:
            k, _, v = part.partition("=")
            pairs.append((k.strip(), v.strip()))
    if not pairs:
        return None
    lines = ["# Netscape HTTP Cookie File"]
    for k, v in pairs:
        lines.append(f".{platform}.com\tTRUE\t/\tTRUE\t0\t{k}\t{v}")
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.write("\n".join(lines))
    f.close()
    return f.name


def probe(url: str, platform: str = "generic") -> dict:
    """只解析不下载，返回 yt-dlp info dict。"""
    opts = _base_opts()
    cf = _cookie_file(platform)
    if cf:
        opts["cookiefile"] = cf
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # yt-dlp 异常类型繁杂，统一转 ParseError
        msg = str(e)
        if "cookie" in msg.lower() and cf:
            msg += "（请检查 cookie 配置格式，应为 SESSDATA=xxx 形式）"
        raise ParseError(f"yt-dlp 解析失败：{msg[:300]}") from e
    finally:
        if cf:
            try:
                os.unlink(cf)
            except OSError:
                pass
    if info is None:
        raise ParseError("未获取到视频信息（可能需要登录或链接失效）")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ParseError("播放列表为空")
        info = entries[0]
    return info


def quality_list(info: dict) -> list[dict]:
    """从 formats 里归纳清晰度选项（去重、按分辨率降序）。"""
    seen, result = set(), []
    for f in info.get("formats") or []:
        fid = str(f.get("format_id", ""))
        note = f.get("format_note") or ""
        height = f.get("height") or 0
        label = note or (f"{height}p" if height else fid)
        if not fid or f.get("vcodec") == "none":
            continue
        key = label
        if key in seen or (not height and not note):
            continue
        seen.add(key)
        result.append({"id": fid, "label": label, "height": height})
    result.sort(key=lambda x: -(x.get("height") or 0))
    return result[:10]


def download(url: str, dest_dir: str, platform: str, options: dict,
             progress, filename_prefix: str = "") -> dict:
    """下载视频，返回 {file_path, file_size}。progress(percent, speed)。"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    prefix = filename_prefix or "%(title).80s"

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = done / total * 100 if total else 0
            speed = d.get("_speed_str") or ""
            try:
                progress(pct, speed.strip())
            except Exception:
                pass
        elif d.get("status") == "finished":
            progress(99.5, "合并中")

    opts = {
        **_base_opts(),
        "outtmpl": str(dest / f"{prefix}.%(ext)s"),
        "progress_hooks": [hook],
        "restrictfilenames": False,
        "windowsfilenames": True,
    }
    cf = _cookie_file(platform)
    if cf:
        opts["cookiefile"] = cf
    quality = options.get("quality") or config.get("default_quality", "best")
    if quality and quality != "best":
        opts["format"] = f"{quality}+bestaudio/{quality}/best"

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        # 指定清晰度不可用时回退到最佳可用画质重试一次
        if quality and quality != "best" and "format" in str(e).lower():
            opts.pop("format", None)
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            except Exception as e2:
                raise ParseError(f"yt-dlp 下载失败：{str(e2)[:300]}") from e2
        else:
            raise ParseError(f"yt-dlp 下载失败：{str(e)[:300]}") from e
    finally:
        if cf:
            try:
                os.unlink(cf)
            except OSError:
                pass

    path = info.get("requested_downloads") or []
    if path:
        fp = path[0].get("filepath")
    else:
        fp = _find_by_prefix(dest, filename_prefix) if filename_prefix else None
        fp = fp or ydl_prepare_filepath(info, dest)
    if not fp or not Path(fp).exists():
        raise ParseError("下载完成但未找到输出文件")
    return {"file_path": str(Path(fp).resolve()),
            "file_size": Path(fp).stat().st_size}


def _find_by_prefix(dest: Path, prefix: str) -> str | None:
    for f in sorted(dest.glob(f"{glob_escape(prefix)}.*")):
        if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".flv", ".m4a", ".mp3", ".part",
                                ".temp", ".tmp"):
            return str(f)
    return None


def glob_escape(s: str) -> str:
    return s.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def ydl_prepare_filepath(info: dict, dest: Path) -> str | None:
    """从 info dict 推断实际落盘路径（yt-dlp 未提供 filepath 时的兜底）。"""
    title = re.sub(r'[\\/:*?"<>|]', "_", (info.get("title") or "video"))[:80].strip()
    for f in sorted(dest.glob(f"{title}.*")):
        if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".flv", ".m4a", ".mp3"):
            return str(f)
    return None
