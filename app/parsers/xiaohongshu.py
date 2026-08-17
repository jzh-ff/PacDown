"""小红书解析器：note 页 __INITIAL_STATE__ 提取视频/图集。

小红书对无 Cookie 请求风控较严，失败时提示用户在设置里配置 Cookie。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import http_download as hd
from .base import ParseError, Parser, VideoInfo, register


def _extract_note_id(url: str) -> str:
    m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{24})", url)
    return m.group(1) if m else ""


def _dig_note(state: dict, note_id: str):
    note_map = ((state.get("note") or {}).get("noteDetailMap") or {})
    for key, val in note_map.items():
        note = (val or {}).get("note") or {}
        if note.get("noteId") and (not note_id or note["noteId"] == note_id):
            return note
    return None


@register
class XiaohongshuParser(Parser):
    platform = "xiaohongshu"
    display_name = "小红书"

    def can_parse(self, url: str) -> bool:
        low = url.lower()
        return "xiaohongshu.com" in low or "xhslink.com" in low

    def parse(self, url: str) -> VideoInfo:
        try:
            final_url = hd.follow_redirect(url, mobile=True) if "xhslink.com" in url else url
        except Exception as e:
            raise ParseError(f"短链跳转失败：{e}") from e
        note_id = _extract_note_id(final_url)

        note = None
        err = ""
        try:
            with hd.client("xiaohongshu") as c:
                r = c.get(final_url if note_id else url)
            if r.status_code != 200:
                raise ParseError(f"HTTP {r.status_code}（大概率风控，请配置 Cookie）")
            m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                          r.text, re.S)
            if not m:
                raise ParseError("页面未包含 __INITIAL_STATE__（请配置小红书 Cookie）")
            import json
            text = m.group(1).replace('undefined', 'null')
            state = json.loads(text)
            note = _dig_note(state, note_id)
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"小红书解析失败：{e}") from e

        if not note:
            raise ParseError("未找到笔记数据（可能已删除或需要 Cookie）")

        user = note.get("user") or {}
        images = [(i.get("urlDefault") or (i.get("url") or "")
                   or (i.get("original") or "")) for i in (note.get("imageList") or [])]
        images = [u.split("!")[0] for u in images if u]
        video = note.get("video") or {}
        stream = ((video.get("media") or {}).get("stream") or {})
        direct = ""
        h264 = stream.get("h264") or []
        if isinstance(h264, list) and h264:
            secs = h264[0].get("masterUrl") or ""
            direct = secs.split("!")[0]
        is_images = bool(images) and not direct

        return VideoInfo(
            platform=self.platform,
            video_id=note.get("noteId") or note_id,
            source_url=url,
            title=(note.get("title") or note.get("desc") or "无标题")[:100],
            description=(note.get("desc") or "").strip(),
            author=user.get("nickname") or "",
            author_id=user.get("userId") or "",
            avatar_url=user.get("avatar") or "",
            cover_url=(images[0] if images else ""),
            duration=int((video.get("capa") or {}).get("duration") or 0),
            publish_time=(note.get("time") or "")[:19].replace("T", " "),
            stats={"liked": note.get("interactInfo", {}).get("likedCount"),
                   "collected": note.get("interactInfo", {}).get("collectedCount")},
            quality_options=[{"id": "best", "label": "原画质", "height": 1080}],
            images=images,
            is_images=is_images,
            raw={"direct_url": direct, "note": {
                k: note.get(k) for k in ("noteId", "title", "desc", "type", "time")
                if note.get(k) is not None}},
        )

    def download(self, info: VideoInfo, dest_dir: str, options: dict, progress,
                 filename_prefix: str = "") -> dict:
        dest_dir = Path(dest_dir)
        base = filename_prefix or hd.safe_filename(info.title)
        if info.is_images:
            paths = []
            for i, img in enumerate(info.images, 1):
                p = dest_dir / f"{base}_{i:02d}.jpg"
                hd.stream_download(img, p, "xiaohongshu", referer="https://www.xiaohongshu.com/")
                paths.append(str(p))
                if progress:
                    progress(i / len(info.images) * 100, f"{i}/{len(info.images)}")
            return {"file_path": paths[0],
                    "file_size": sum(Path(x).stat().st_size for x in paths),
                    "images": paths}
        direct = (info.raw or {}).get("direct_url") or ""
        if not direct:
            raise ParseError("该笔记无视频或缺少直链")
        p = dest_dir / f"{base}.mp4"
        return hd.stream_download(direct, p, "xiaohongshu", progress,
                                  referer="https://www.xiaohongshu.com/")
