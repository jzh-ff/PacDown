"""通用兜底解析器：yt-dlp 引擎（YouTube、西瓜、微博等上千站点）。"""
from __future__ import annotations

from datetime import datetime

from . import ytdlp_engine
from .base import Parser, VideoInfo


class GenericParser(Parser):
    platform = "generic"
    display_name = "通用（yt-dlp）"

    def can_parse(self, url: str) -> bool:
        return True  # 兜底

    def parse(self, url: str) -> VideoInfo:
        info = ytdlp_engine.probe(url, platform="generic")
        ts = info.get("timestamp") or info.get("release_timestamp")
        return VideoInfo(
            platform=self.platform,
            video_id=str(info.get("id") or ""),
            source_url=url,
            title=info.get("title") or "",
            description=info.get("description") or "",
            author=info.get("uploader") or info.get("channel") or "",
            author_id=str(info.get("uploader_id") or info.get("channel_id") or ""),
            cover_url=info.get("thumbnail") or "",
            duration=int(info.get("duration") or 0),
            publish_time=datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "",
            stats={"view": info.get("view_count"),
                   "like": info.get("like_count"),
                   "comment": info.get("comment_count")},
            quality_options=ytdlp_engine.quality_list(info),
            raw={"engine": "yt-dlp",
                 "extractor": info.get("extractor_key") or info.get("extractor"),
                 "webpage_url": info.get("webpage_url")},
        )

    def download(self, info, dest_dir: str, options: dict, progress,
                 filename_prefix: str = "") -> dict:
        result = ytdlp_engine.download(info.source_url, dest_dir, "generic",
                                       options, progress,
                                       filename_prefix=filename_prefix)
        return result
