"""后处理：MP3 提取（ffmpeg）与 B站弹幕下载（XML）。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx

from . import config


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_mp3(video_path: str, out_path: str | None = None) -> str:
    """从视频提取 MP3，成功返回路径，失败抛异常。"""
    if not ffmpeg_available():
        raise RuntimeError("未检测到 ffmpeg，无法提取音频。请安装 ffmpeg 并加入 PATH")
    src = Path(video_path)
    out = Path(out_path or src.with_suffix(".mp3"))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg 失败：{(p.stderr or '')[:200]}")
    return str(out.resolve())


def fetch_bilibili_comments(aid: int, count: int = 20) -> list[dict]:
    """抓取B站视频热评第一页（免登录接口）。aid 为 av 号数字。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126",
        "Referer": "https://www.bilibili.com/",
    }
    cookie = config.get("bilibili_cookie", "")
    if cookie:
        headers["Cookie"] = cookie
    url = ("https://api.bilibili.com/x/v2/reply?type=1&sort=2"
           f"&pn=1&ps={count}&oid={aid}")
    with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
        r = c.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"评论接口返回 code={data.get('code')}")
    replies = ((data.get("data") or {}).get("replies")) or []
    out = []
    for rp in replies:
        member = (rp.get("member") or {})
        out.append({
            "user": member.get("uname") or "",
            "content": ((rp.get("content") or {}).get("message") or "").strip(),
            "like": rp.get("like") or 0,
            "time": _fmt_ts(rp.get("ctime")),
        })
    return out


def _fmt_ts(t) -> str:
    if not t:
        return ""
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return ""


def download_danmaku(cid: int, dest: str) -> str:
    """下载B站弹幕 XML（api.bilibili.com/x/v1/dm/list.so）。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126",
        "Referer": "https://www.bilibili.com/",
    }
    cookie = config.get("bilibili_cookie", "")
    if cookie:
        headers["Cookie"] = cookie
    with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
        r = c.get(f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}", headers=headers)
        r.raise_for_status()
    text = r.text
    if "<d " not in text and "<d>" not in text:
        raise RuntimeError("弹幕接口返回内容为空（可能参数失效）")
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return str(out.resolve())
