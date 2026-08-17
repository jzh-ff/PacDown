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


def _extract_page_no(url: str) -> int:
    """URL 中的 ?p=N 分P序号（从 1 开始），无则 0。"""
    m = re.search(r"[?&]p=(\d+)", url)
    return int(m.group(1)) if m else 0


def list_related(url: str) -> dict:
    """列出该视频的分P与所属合集（供批量下载）。

    返回 {"parts": [...], "season": {...} | None}；entries 元素：
    {url, title, cover, duration, index}。异常时返回空结构。
    """
    out = {"parts": [], "season": None}
    bvid = _extract_bvid(url)
    if not bvid.startswith("BV"):
        return out
    try:
        view = BilibiliParser()._web_view(bvid)
    except Exception:
        return out
    pages = view.get("pages") or []
    if len(pages) > 1:
        out["parts"] = [{
            "url": f"https://www.bilibili.com/video/{bvid}?p={p.get('page', i)}",
            "title": p.get("part") or f"P{p.get('page', i)}",
            "cover": p.get("first_frame") or "",
            "duration": int(p.get("duration") or 0),
            "index": p.get("page", i),
        } for i, p in enumerate(pages, 1)]
    season = view.get("ugc_season") or {}
    sections = season.get("sections") or []
    episodes = [ep for s in sections for ep in (s.get("episodes") or [])]
    if episodes:
        entries = []
        for i, ep in enumerate(episodes, 1):
            eb = ep.get("bvid") or ""
            if not eb:
                continue
            entries.append({
                "url": f"https://www.bilibili.com/video/{eb}",
                "title": ep.get("title") or eb,
                "cover": (ep.get("arc") or {}).get("pic") or "",
                "duration": 0,
                "index": i,
            })
        if entries:
            out["season"] = {"title": season.get("title") or "合集",
                             "entries": entries}
    return out


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
        view = {}
        try:
            view = self._web_view(bvid)
            vi.raw["web_view"] = view
        except Exception:
            pass

        # ?p=N 分P：video_id 加后缀避免各分P互相判重，标题换成分P名
        p_no = _extract_page_no(url)
        if p_no > 0 and bvid:
            pages = view.get("pages") or []
            if p_no <= len(pages):
                pg = pages[p_no - 1]
                vi.video_id = f"{bvid}_p{p_no}"
                vi.title = pg.get("part") or vi.title
                vi.duration = int(pg.get("duration") or vi.duration or 0)
                vi.cover_url = pg.get("first_frame") or vi.cover_url
                if not vi.title or vi.title == (view.get("title") or ""):
                    vi.title = f"{view.get('title') or ''} P{p_no}"
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
        # 兼容分P id（{bvid}_p{N}）：取对应分P的 cid
        p_no = 0
        m = re.match(r"(BV[0-9A-Za-z]{10})_p(\d+)$", bvid)
        if m:
            bvid, p_no = m.group(1), int(m.group(2))
        view = self._web_view(bvid)
        pages = view.get("pages") or []
        if p_no and p_no <= len(pages):
            return pages[p_no - 1].get("cid") or None
        cid = view.get("cid") or (pages[0].get("cid") if pages else None)
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
