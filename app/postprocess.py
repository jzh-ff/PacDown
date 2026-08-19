"""后处理：MP3 提取（ffmpeg）与 B站弹幕下载（XML）。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx

from . import config


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_duration(video_path: str) -> float:
    """视频时长（秒），失败返回 0。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float((p.stdout or "").strip() or 0)
    except (ValueError, subprocess.SubprocessError):
        return 0.0


def extract_frame(video_path: str, out_path: str,
                  at_sec: float | None = None, percent: float = 0.0) -> str:
    """从视频截一帧存为 JPG。percent=0.2 表示 20% 处；at_sec 优先。"""
    if not ffmpeg_available():
        raise RuntimeError("未检测到 ffmpeg，无法截帧")
    if at_sec is None:
        dur = ffprobe_duration(video_path)
        at_sec = dur * percent if dur > 0 else 2.0
        at_sec = max(0.5, at_sec)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at_sec:.2f}",
           "-i", video_path, "-frames:v", "1", "-q:v", "3", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"截帧失败：{(p.stderr or '')[:200]}")
    return str(out.resolve())


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


def _dy_time(t) -> str:
    """抖音 createTime 兼容：字符串原样截断，数字按时间戳格式化。"""
    if not t:
        return ""
    s = str(t)
    if len(s) == 10 and s.isdigit():
        from datetime import datetime
        try:
            return datetime.fromtimestamp(int(s)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return s
    return s[:16]


def fetch_douyin_comments(aweme_id: str, count: int = 20) -> list[dict]:
    """抓取抖音评论（iesdouyin v2 接口，免 Cookie，2026-08 实测可用）。"""
    headers = {
        "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                       "Mobile/15E148 Safari/604.1"),
        "Referer": "https://www.douyin.com/",
    }
    cookie = config.get("douyin_cookie", "")
    if cookie:
        headers["Cookie"] = cookie
    url = (f"https://www.iesdouyin.com/web/api/v2/comment/list/"
           f"?aweme_id={aweme_id}&cursor=0&count={count}&appid=1128")
    with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
        r = c.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
    comments = data.get("comments") or []
    out = []
    for cm in comments:
        user = cm.get("user") or {}
        text = (cm.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "user": user.get("nickname") or "",
            "content": text,
            "like": cm.get("digg_count") or 0,
            "time": _dy_time(cm.get("createTime")),
            "ip": str(cm.get("ip_label") or ""),
        })
    return out


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


# ---------------- B站 CC 字幕 ----------------

def fetch_bilibili_subtitle(bvid: str, cid: int) -> str | None:
    """拉取B站 CC 字幕并转为 SRT 文本；无字幕返回 None。

    走 player/v2 接口（免签名，2026-08 实测可用），取首条可用字幕轨。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126",
        "Referer": "https://www.bilibili.com/",
    }
    cookie = config.get("bilibili_cookie", "")
    if cookie:
        headers["Cookie"] = cookie
    with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
        r = c.get(f"https://api.bilibili.com/x/player/v2?cid={cid}&bvid={bvid}",
                  headers=headers)
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"字幕接口返回 code={data.get('code')}")
    subs = ((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return None
    # 优先级：AI 中文 > 中文字幕 > 第一条
    def score(s):
        lan = (s.get("lan") or "").lower()
        if lan.startswith("ai-") or "zh" in lan:
            return 0 if lan.startswith("ai") else 1
        return 2
    best = min(subs, key=score)
    url = best.get("subtitle_url") or ""
    if not url.startswith("http"):
        url = "https:" + url
    with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
        r = c.get(url, headers=headers)
        r.raise_for_status()
        body = (r.json().get("body") or [])
    if not body:
        return None
    return _srt_from_body(body)


def _srt_from_body(body: list[dict]) -> str:
    """字幕 JSON body → SRT 文本。"""
    out = []
    for i, it in enumerate(body, 1):
        start = float(it.get("from") or 0)
        end = float(it.get("to") or start + 1)
        text = (it.get("content") or "").strip().replace("\n", " ")
        if not text:
            continue
        out.append(f"{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
    return "\n".join(out)


def _srt_ts(sec: float) -> str:
    sec = max(0, sec)
    ms = int(round((sec - int(sec)) * 1000))
    s = int(sec) % 60
    m = (int(sec) // 60) % 60
    h = int(sec) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
