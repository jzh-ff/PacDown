"""HTTP 直链流式下载工具（抖音/快手/小红书共用）。"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from .. import config

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
             "Mobile/15E148 Safari/604.1")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 各平台防盗链 Referer
REFERERS = {
    "douyin": "https://www.douyin.com/",
    "kuaishou": "https://www.kuaishou.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "bilibili": "https://www.bilibili.com/",
}


def client(platform: str = "", mobile: bool = False) -> httpx.Client:
    """带平台 cookie 与代理的 httpx 客户端。"""
    headers = {"User-Agent": UA_MOBILE if mobile else UA_DESKTOP}
    cookie = config.get(f"{platform}_cookie", "") if platform else ""
    if cookie:
        headers["Cookie"] = cookie
    return httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(20, read=60),
        proxy=config.get("http_proxy") or None,
    )


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" ._")
    return (name[:max_len]).rstrip(" .") or "video"


def stream_download(url: str, dest: Path, platform: str, progress=None,
                    referer: str = "", mobile: bool = False) -> dict:
    """流式下载 url 到 dest，返回 {file_path, file_size}。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Referer": referer} if referer else {}
    with client(platform, mobile=mobile) as c:
        with c.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1 << 18):
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        try:
                            progress(done / total * 100,
                                     f"{done / 1024 / 1024:.1f}MB")
                        except Exception:
                            pass
    size = dest.stat().st_size
    if progress:
        progress(100, "完成")
    if size < 1024:
        raise ValueError(f"下载内容异常（仅 {size} 字节），链接可能已失效")
    return {"file_path": str(dest.resolve()), "file_size": size}


def follow_redirect(url: str, platform: str = "", mobile: bool = True) -> str:
    """跟随重定向返回最终 URL（不下载 body）。"""
    with client(platform, mobile=mobile) as c:
        r = c.head(url)
        if r.status_code >= 400:  # 部分短链服务不支持 HEAD
            r = c.get(url)
        r.raise_for_status()
        return str(r.url)


def fetch_json_in_html(html: str, marker: str) -> dict | None:
    """提取 html 中 `marker = {...};` 形式的内嵌 JSON。"""
    idx = html.find(marker)
    if idx == -1:
        return None
    start = html.find("{", idx)
    if start == -1:
        return None
    depth, in_str, escape = 0, False, False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                import json
                try:
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
