"""抖音解析器：无水印直链解析。

2025 年起抖音收紧了公开通道（分享页 SSR 数据移除、v2 iteminfo 接口停用），
因此采用双路径：
  路径 A（推荐）：用户在设置中配置浏览器 Cookie（不必登录）→ 请求 PC 页面，
           从 SSR 数据流（self.__pace_f / 内嵌 JSON）中提取 playAddr 无水印直链
  路径 B（免配置）：iesdouyin 移动端分享页 _ROUTER_DATA（部分内容仍可能携带）
两条路径都失败时给出明确的 Cookie 配置指引。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import httpx

from .. import config
from . import http_download as hd
from .base import ParseError, Parser, VideoInfo, register

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _extract_item_id(url: str) -> str:
    m = re.search(r"/(?:video|note|slides)/(?:\d+/)?(\d{15,})", url)
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{15,})", url)
    return m.group(1) if m else ""


def _extract_mix_id(url: str) -> str:
    """合集页链接 douyin.com/collection/{mix_id} 的 mix_id。"""
    m = re.search(r"/collection/(\d+)", url)
    return m.group(1) if m else ""


# ---------- 合集（mix） ----------

def list_mix(mix_id: str, limit: int = 50) -> dict:
    """列出合集内视频：{name, entries:[{video_id,title,url,cover,duration}]}。

    走 douyin web 合集接口 aweme/v1/web/mix/aweme（2026-08 实测免签名可用）；
    Cookie 路径优先，免 Cookie 兜底，都失败时给出配置指引。
    """
    cookie = (config.get("douyin_cookie") or "").strip()
    last_err = ""
    if cookie:
        try:
            r = _mix_from_api(mix_id, limit, cookie)
            if r["entries"]:
                return r
            last_err = "Cookie 已配置但接口未返回合集视频（Cookie 可能过期）"
        except Exception as e:
            last_err = f"带 Cookie 请求失败：{e}"
    try:
        r = _mix_from_api(mix_id, limit, "")
        if r["entries"]:
            return r
        last_err = last_err or "接口未返回合集视频"
    except Exception as e:
        last_err = last_err or str(e)
    hint = "。请在「设置 → 平台 Cookie」中粘贴抖音 Cookie 后重试（浏览器打开 douyin.com → F12 → 网络 → 任一请求的 Cookie 头），无需登录账号"
    raise ParseError(f"抖音合集解析失败：{last_err}{hint}")


def _mix_from_api(mix_id: str, limit: int, cookie: str = "") -> dict:
    """web 合集接口（cursor 分页）；Referer 指向合集页更像真实浏览。"""
    headers = {"User-Agent": UA_PC,
               "Referer": f"https://www.douyin.com/collection/{mix_id}"}
    if cookie:
        headers["Cookie"] = cookie
    out: list[dict] = []
    cursor = 0
    name = ""
    with httpx.Client(timeout=httpx.Timeout(20, read=40),
                      proxy=config.get("http_proxy") or None) as c:
        while len(out) < limit:
            r = c.get("https://www.douyin.com/aweme/v1/web/mix/aweme/",
                      params={"mix_id": mix_id, "cursor": cursor, "count": 20,
                              "device_platform": "webapp", "aid": "6383"},
                      headers=headers)
            r.raise_for_status()
            data = r.json()
            awemes = data.get("aweme_list") or []
            if not awemes:
                break
            out.extend(awemes)
            mix_info = awemes[0].get("mix_info") or {}
            name = name or (mix_info.get("mix_name") or "")
            if not data.get("has_more"):
                break
            cursor = data.get("cursor") or (cursor + len(awemes))
    return {"name": name, "entries": [_mix_entry(it) for it in out[:limit]]}


def _mix_entry(aweme: dict) -> dict:
    aweme_id = str(aweme.get("aweme_id") or aweme.get("awemeId") or "")
    video = aweme.get("video") or {}
    cover = ((video.get("cover") or {}).get("url_list") or [""])
    return {
        "video_id": aweme_id,
        "title": (aweme.get("desc") or "无标题").splitlines()[0][:80],
        "url": f"https://www.douyin.com/video/{aweme_id}",
        "cover": cover[0] if cover else "",
        "duration": int(video.get("duration") or 0) // 1000,
    }


# ---------- PC SSR 提取 ----------

def _from_pc_page(item_id: str, cookie: str) -> dict | None:
    """带 Cookie 请求 PC 视频页，从 SSR 数据流提取 aweme 数据。"""
    headers = {
        "User-Agent": UA_PC,
        "Referer": "https://www.douyin.com/",
        "Cookie": cookie,
    }
    with httpx.Client(headers=headers, follow_redirects=True,
                      timeout=httpx.Timeout(20, read=40),
                      proxy=config.get("http_proxy") or None) as c:
        r = c.get(f"https://www.douyin.com/video/{item_id}")
    if r.status_code != 200:
        return None
    return _aweme_from_html(r.text)


def _aweme_from_html(html: str) -> dict | None:
    """从页面任意位置挖掘 aweme 视频数据（playAddr / play_addr）。"""
    # 形态1: "playAddr":{"uri":"...","urlList":["https://..."]}
    m = re.search(
        r'"playAddr":\s*\{[^{}]*?"uri":\s*"([^"]+)"[^{}]*?"urlList":\s*\["([^"]+)"',
        html)
    if not m:  # 字段顺序变体
        m = re.search(
            r'"playAddr":\s*\{[^{}]*?"urlList":\s*\["([^"]+)"[^{}]*?"uri":\s*"([^"]+)"',
            html)
        if m:
            return _aweme_slim(uri=m.group(2), url=m.group(1), html=html)
    if m:
        return _aweme_slim(uri=m.group(1), url=m.group(2), html=html)
    # 形态2: play_addr（下划线风格）
    m = re.search(
        r'"play_addr":\s*\{[^{}]*?"uri":\s*"([^"]+)"[^{}]*?"url_list":\s*\["([^"]+)"',
        html)
    if m:
        return _aweme_slim(uri=m.group(1), url=m.group(2), html=html)
    return None


def _aweme_slim(uri: str, url: str, html: str) -> dict:
    """组装精简 aweme 结构（尽量抽取标题/作者等旁路字段）。"""
    def nearby(pattern):
        mm = re.search(pattern, html)
        return mm.group(1) if mm else ""

    title = nearby(r'"desc":\s*"((?:[^"\\]|\\.){1,200}?)"') or "无标题"
    try:
        title = json.loads(f'"{title}"')
    except json.JSONDecodeError:
        pass
    nickname = nearby(r'"nickname":\s*"((?:[^"\\]|\\.){1,60}?)"')
    try:
        nickname = json.loads(f'"{nickname}"') if nickname else ""
    except json.JSONDecodeError:
        nickname = ""
    unique_id = nearby(r'"uniqueId":\s*"([^"]{1,60}?)"') or nearby(
        r'"unique_id":\s*"([^"]{1,60}?)"')
    create_time = nearby(r'"createTime":\s*(\d{10,13})') or nearby(r'"create_time":\s*(\d{10,13})')
    images = re.findall(
        r'"urlList":\s*\["(https?://[^"]+?)"', html)
    images = [u for u in images if "douyinpic" in u or "byteimg" in u or "aweme" in u][:40]

    mix_id = nearby(r'"mixId":\s*"(\d+)"') or nearby(r'"mix_id":\s*"(\d+)"')
    mix_name = nearby(r'"mixName":\s*"((?:[^"\\]|\\.){1,120}?)"') or nearby(
        r'"mix_name":\s*"((?:[^"\\]|\\.){1,120}?)"')
    if mix_name:
        try:
            mix_name = json.loads(f'"{mix_name}"')
        except (json.JSONDecodeError, ValueError):
            pass

    return {
        "aweme_id": nearby(r'"awemeId":\s*"(\d+)"') or nearby(r'"aweme_id":\s*(\d+)') or "",
        "desc": title,
        "author": {"nickname": nickname, "unique_id": unique_id, "short_id": ""},
        "mix_info": {"mix_id": mix_id, "mix_name": mix_name} if mix_id else {},
        "video": {
            "play_addr": {"uri": uri, "url_list": [url]},
            "cover": {"url_list": [nearby(r'"cover":\s*\{\s*"[^"]+":\s*\[\s*"(https?://[^"]+)"')]},
            "duration": int(nearby(r'"duration":\s*(\d+)') or 0),
        },
        "create_time": int(create_time) if create_time else 0,
        "statistics": {
            k: int(v) for k, v in (
                ("digg_count", nearby(r'"diggCount":\s*(\d+)')),
                ("comment_count", nearby(r'"commentCount":\s*(\d+)')),
                ("share_count", nearby(r'"shareCount":\s*(\d+)')),
                ("collect_count", nearby(r'"collectCount":\s*(\d+)')),
                ("play_count", nearby(r'"playCount":\s*(\d+)')),
            ) if v
        },
        "images": [{"url_list": [u]} for u in images[:40]],
    }


def _pick_router_data(data: dict) -> dict | None:
    """从 iesdouyin _ROUTER_DATA 中找 item_list（老结构，保留兼容）。"""
    loader = data.get("loaderData") or {}
    for key, val in loader.items():
        if not isinstance(val, dict):
            continue
        res = val.get("videoInfoRes") or {}
        lst = res.get("item_list") or []
        if lst:
            return lst[0]
    return None


@register
class DouyinParser(Parser):
    platform = "douyin"
    display_name = "抖音"

    def can_parse(self, url: str) -> bool:
        low = url.lower()
        return "douyin.com" in low or "iesdouyin.com" in low

    def parse(self, url: str) -> VideoInfo:
        try:
            final_url = hd.follow_redirect(url, mobile=True) \
                if "v.douyin.com" in url else url
        except Exception as e:
            raise ParseError(f"短链跳转失败：{e}") from e
        item_id = _extract_item_id(final_url)
        if not item_id:
            raise ParseError("无法从链接中提取视频 ID，请复制完整分享链接")

        aweme = self._fetch_item(item_id)
        return self._to_video_info(url, item_id, aweme)

    def _fetch_item(self, item_id: str) -> dict:
        """路径 A（有 Cookie 走 PC SSR）→ 路径 B（iesdouyin 分享页）。"""
        cookie = (config.get("douyin_cookie") or "").strip()
        last_err = ""
        if cookie:
            try:
                aweme = _from_pc_page(item_id, cookie)
                if aweme:
                    return aweme
                last_err = "Cookie 已配置但页面未返回视频数据（Cookie 可能过期）"
            except Exception as e:
                last_err = f"PC 页面请求失败：{e}"
        # 路径 B：免 cookie 的移动端分享页
        try:
            with hd.client("douyin", mobile=True) as c:
                r = c.get(f"https://www.iesdouyin.com/share/video/{item_id}/",
                          headers={"Referer": "https://www.douyin.com/"})
            if r.status_code == 200:
                data = hd.fetch_json_in_html(r.text, "_ROUTER_DATA")
                if data:
                    aweme = _pick_router_data(data)
                    if aweme:
                        return aweme
                last_err = last_err or "分享页未携带视频数据"
        except Exception as e:
            last_err = str(e)

        hint = "。请在「设置 → 平台 Cookie」中粘贴抖音 Cookie（浏览器打开 douyin.com → F12 → 网络 → 任一请求的 Cookie 头），无需登录账号"
        raise ParseError(f"抖音解析失败：{last_err}{hint}")

    def _to_video_info(self, url: str, item_id: str, aweme: dict) -> VideoInfo:
        author = aweme.get("author") or {}
        stats = aweme.get("statistics") or {}
        video = aweme.get("video") or {}
        play = video.get("play_addr") or {}
        uri = play.get("uri") or ""
        urls = play.get("url_list") or []
        direct = ""
        if urls:
            direct = urls[0].replace("playwm", "play")
        elif uri:
            direct = f"https://www.douyin.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0"
        images = [img.get("url_list", [""])[0]
                  for img in (aweme.get("images") or []) if img.get("url_list")]
        desc_full = (aweme.get("desc") or "").strip()
        mix = aweme.get("mix_info") or {}
        collection = {}
        if mix.get("mix_id"):
            collection = {"platform": "douyin", "id": str(mix["mix_id"]),
                          "name": mix.get("mix_name") or "合集"}

        return VideoInfo(
            platform=self.platform,
            video_id=str(aweme.get("aweme_id") or item_id),
            source_url=url,
            title=(aweme.get("desc") or "无标题").splitlines()[0][:100],
            description=desc_full,
            author=author.get("nickname") or "",
            author_id=author.get("unique_id") or author.get("short_id") or "",
            avatar_url=((author.get("avatar_thumb") or {}).get("url_list") or [""])[0]
            if isinstance(author.get("avatar_thumb"), dict) else "",
            cover_url=((video.get("cover") or {}).get("url_list") or [""])[0],
            duration=int(video.get("duration") or 0) // 1000,
            publish_time=datetime.fromtimestamp(
                (aweme.get("create_time") or 0) / 1000
                if len(str(aweme.get("create_time") or "")) > 10
                else (aweme.get("create_time") or 0)
            ).strftime("%Y-%m-%d %H:%M:%S") if aweme.get("create_time") else "",
            stats={
                "play": stats.get("play_count"),
                "like": stats.get("digg_count"),
                "comment": stats.get("comment_count"),
                "share": stats.get("share_count"),
                "collect": stats.get("collect_count"),
            },
            quality_options=[{"id": "best", "label": "原画（无水印）", "height": 1080}],
            images=images,
            is_images=bool(images),
            raw={"item_id": item_id, "direct_url": direct, "aweme": _slim(aweme),
                 **({"collection": collection} if collection else {})},
        )

    def download(self, info, dest_dir: str, options: dict, progress,
                 filename_prefix: str = "") -> dict:
        dest_dir = Path(dest_dir)
        base = filename_prefix or hd.safe_filename(info.title)
        if info.is_images and info.images:
            paths = []
            for i, img in enumerate(info.images, 1):
                p = dest_dir / f"{base}_{i:02d}.jpg"
                hd.stream_download(img, p, "douyin", progress=None,
                                   referer="https://www.douyin.com/", mobile=True,
                                   rate_limit=options.get("_rate_limit") or 0)
                paths.append(str(p))
                if progress:
                    progress(i / len(info.images) * 100, f"{i}/{len(info.images)}")
            return {"file_path": paths[0],
                    "file_size": sum(Path(x).stat().st_size for x in paths),
                    "images": paths}
        direct = (info.raw or {}).get("direct_url") or ""
        if not direct:
            raise ParseError("缺少直链，请重新解析")
        p = dest_dir / f"{base}.mp4"
        return hd.stream_download(direct, p, "douyin", progress,
                                  referer="https://www.douyin.com/", mobile=True,
                                  rate_limit=options.get("_rate_limit") or 0)


def _slim(aweme: dict) -> dict:
    keep = ("aweme_id", "desc", "create_time", "duration",
            "statistics", "video", "images", "author", "mix_info")
    return {k: aweme.get(k) for k in keep if aweme.get(k) is not None}
