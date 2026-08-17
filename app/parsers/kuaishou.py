"""快手解析器：短链重定向 → 页面 __NEXT_DATA__ / videoData 提取无水印直链。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import http_download as hd
from .base import ParseError, Parser, VideoInfo, register


def _find_photo_key(data):
    """递归查找 __NEXT_DATA__ 里含 photo 的节点。"""
    from . import http_download as h
    return data


def _dig_photo(obj):
    """从嵌套 JSON 中找到 photo 对象（含 photoId/mainMvUrls）。"""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if ("photoId" in cur or "photo_id" in cur) and (
                "mainMvUrls" in cur or "photoUrl" in cur or "manifest" in cur
            ):
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


@register
class KuaishouParser(Parser):
    platform = "kuaishou"
    display_name = "快手"

    def can_parse(self, url: str) -> bool:
        low = url.lower()
        return any(h in low for h in ("kuaishou.com", "chenzhongtech.com", "gifshow.com"))

    def parse(self, url: str) -> VideoInfo:
        try:
            final_url = hd.follow_redirect(url, mobile=True) if "v.kuaishou.com" in url or "v.chenzhongtech.com" in url else url
        except Exception as e:
            raise ParseError(f"短链跳转失败：{e}") from e

        photo = None
        page_err = ""
        for u in (final_url, url):
            try:
                with hd.client("kuaishou", mobile=True) as c:
                    r = c.get(u)
                if r.status_code != 200:
                    page_err = f"HTTP {r.status_code}"
                    continue
                for marker in ("__NEXT_DATA__", "videoData"):
                    data = hd.fetch_json_in_html(r.text, f"{marker} =") or \
                           hd.fetch_json_in_html(r.text, f'"{marker}":')
                    if isinstance(data, dict):
                        found = _dig_photo(data)
                        if found:
                            photo = found
                            break
                if photo:
                    break
                m = re.search(r"data-paging=", r.text)
                if not photo and not m:
                    page_err = "页面未包含视频数据（可能需要 Cookie）"
            except Exception as e:
                page_err = str(e)

        if not photo:
            raise ParseError(f"快手解析失败：{page_err}。可尝试在设置中配置快手 Cookie")

        mv_urls = photo.get("mainMvUrls") or []
        direct = ""
        if mv_urls:
            direct = (mv_urls[0].get("url") or "").replace("playwm", "play")
        if not direct:
            direct = photo.get("photoUrl") or ""
        if not direct:
            raise ParseError("未找到视频地址")

        ts = photo.get("timestamp") or 0
        return VideoInfo(
            platform=self.platform,
            video_id=str(photo.get("photoId") or photo.get("photo_id") or ""),
            source_url=url,
            title=(photo.get("caption") or "无标题").strip()[:100] or "无标题",
            author=(photo.get("userName") or
                    ((photo.get("user") or {}).get("user_name") or "")),
            author_id=str(photo.get("userId") or (photo.get("user") or {}).get("user_id") or ""),
            cover_url=photo.get("coverUrl") or photo.get("poster") or "",
            duration=int(photo.get("duration") or 0),
            publish_time=datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
            .strftime("%Y-%m-%d %H:%M:%S") if ts else "",
            stats={"view": photo.get("viewCount"),
                   "like": photo.get("likeCount"),
                   "comment": photo.get("commentCount")},
            quality_options=[{"id": "best", "label": "原画（无水印）", "height": 1080}],
            raw={"direct_url": direct, "photo": {
                k: photo.get(k) for k in ("photoId", "caption", "duration",
                                          "timestamp", "likeCount", "viewCount")
                if photo.get(k) is not None}},
        )

    def download(self, info: VideoInfo, dest_dir: str, options: dict, progress) -> dict:
        direct = (info.raw or {}).get("direct_url") or ""
        if not direct:
            raise ParseError("缺少直链，请重新解析")
        p = Path(dest_dir) / f"{hd.safe_filename(info.title)}.mp4"
        return hd.stream_download(direct, p, "kuaishou", progress,
                                  referer="https://www.kuaishou.com/", mobile=True)
