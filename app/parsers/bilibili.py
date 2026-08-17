"""B站解析器：yt-dlp 引擎解析/下载 + 视频页 API 补充数据 + 弹幕 cid。"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from .. import config
from . import ytdlp_engine
from .base import Parser, ParseError, VideoInfo, register


def _extract_bvid(url: str) -> str:
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        return m.group(1)
    m = re.search(r"av(\d+)", url, re.I)
    if m:
        return f"av{m.group(1)}"
    return ""


@register
class BilibiliParser(Parser):
    platform = "bilibili"
    display_name = "哔哩哔哩"

    def can_parse(self, url: str) -> bool:
        low = url.lower()
        return "bilibili.com" in low or "b23.tv" in low

    def parse(self, url: str) -> VideoInfo:
        info = ytdlp_engine.probe(url, platform="bilibili")
        bvid = _extract_bvid(url) or (info.get("id") or "")

        vi = VideoInfo(
            platform=self.platform,
            video_id=bvid,
            source_url=url,
            title=info.get("title") or "",
            description=info.get("description") or "",
            author=info.get("uploader") or "",
            author_id=str(info.get("uploader_id") or ""),
            avatar_url=info.get("uploader") and "" or "",
            cover_url=info.get("thumbnail") or "",
            duration=int(info.get("duration") or 0),
            publish_time=datetime.fromtimestamp(
                info.get("timestamp") or info.get("release_timestamp") or 0
            ).strftime("%Y-%m-%d %H:%M:%S") if (info.get("timestamp") or info.get("release_timestamp")) else "",
            stats={
                "play": info.get("view_count"),
                "like": info.get("like_count"),
                "comment": info.get("comment_count"),
            },
            quality_options=ytdlp_engine.quality_list(info),
            raw={"engine": "yt-dlp", "info": _slim_info(info)},
        )

        # 用网页 API 补充精确发布时间/分区等（失败不影响主流程）
        try:
            vi.raw["web_view"] = self._web_view(bvid)
        except Exception:
            pass
        return vi

    def _web_view(self, bvid: str) -> dict:
        if not bvid.startswith("BV"):
            return {}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}
        cookie = config.get("bilibili_cookie", "")
        if cookie:
            headers["Cookie"] = cookie
        with httpx.Client(timeout=10, proxy=config.get("http_proxy") or None) as c:
            r = c.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                      headers=headers)
            data = r.json()
        return data.get("data") or {}

    def get_cid(self, bvid: str) -> int | None:
        view = self._web_view(bvid)
        cid = view.get("cid") or (view.get("pages") or [{}])[0].get("cid")
        return cid if cid else None

    def download(self, info, dest_dir: str, options: dict, progress,
                 filename_prefix: str = "") -> dict:
        result = ytdlp_engine.download(
            info.source_url, dest_dir, "bilibili", options, progress,
            filename_prefix=filename_prefix,
        )
        return result


def _slim_info(info: dict) -> dict:
    """sidecar 里保留有价值的字段，剔除巨大的 formats 明细。"""
    keep = ("id", "title", "description", "uploader", "uploader_id", "duration",
            "view_count", "like_count", "comment_count", "timestamp",
            "upload_date", "webpage_url", "thumbnail", "resolution")
    return {k: info.get(k) for k in keep if info.get(k) is not None}
