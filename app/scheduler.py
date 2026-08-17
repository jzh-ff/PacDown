"""订阅调度：定时拉取博主最新视频，与库比对后自动入队下载。"""
from __future__ import annotations

import json
import re
import threading

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from . import config, database
from .downloader import manager
from .parsers.http_download import client as http_client

SUPPORTED = {"bilibili", "douyin", "xiaohongshu", "kuaishou"}


def parse_uploader_url(url: str) -> tuple[str, str]:
    """从博主主页链接提取 (platform, uploader_id)。"""
    low = url.lower()
    if "space.bilibili.com" in low or "bilibili.com" in low:
        m = re.search(r"space\.bilibili\.com/(\d+)", url)
        if not m:
            raise ValueError("B站订阅链接格式：https://space.bilibili.com/{数字ID}")
        return "bilibili", m.group(1)
    if "douyin.com/user/" in low:
        m = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", url)
        if not m:
            raise ValueError("抖音订阅链接格式：https://www.douyin.com/user/{sec_uid}")
        return "douyin", m.group(1)
    if "xiaohongshu.com/user/profile/" in low:
        m = re.search(r"/user/profile/([0-9a-f]{24})", url)
        if not m:
            raise ValueError("小红书订阅链接格式：https://www.xiaohongshu.com/user/profile/{用户ID}")
        return "xiaohongshu", m.group(1)
    if "kuaishou.com/profile/" in low:
        m = re.search(r"kuaishou\.com/profile/([A-Za-z0-9_\-]+)", url)
        if not m:
            raise ValueError("快手订阅链接格式：https://www.kuaishou.com/profile/{用户ID}")
        return "kuaishou", m.group(1)
    raise ValueError("暂不支持该平台的订阅（当前支持：B站、抖音、小红书、快手）")


def fetch_uploader_info(platform: str, uploader_id: str) -> dict:
    """拉取博主名称/头像。"""
    if platform == "bilibili":
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://space.bilibili.com/"}
        cookie = config.get("bilibili_cookie", "")
        if cookie:
            headers["Cookie"] = cookie
        with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
            r = c.get(f"https://api.bilibili.com/x/web-interface/card?mid={uploader_id}",
                      headers=headers)
            data = (r.json().get("data") or {}).get("card") or {}
        return {"name": data.get("name") or f"UP主{uploader_id}",
                "avatar": data.get("face") or ""}
    if platform == "douyin":
        try:
            with http_client("douyin", mobile=True) as c:
                r = c.get(f"https://www.douyin.com/user/{uploader_id}")
            m = re.search(r'"nickname":"(.*?)"', r.text)
            a = re.search(r'"avatar_thumb":\{[^}]*?"url_list":\["(.*?)"', r.text)
            return {"name": (m.group(1) if m else f"博主{uploader_id[:8]}"),
                    "avatar": (a.group(1) if a else "")}
        except Exception:
            return {"name": f"博主{uploader_id[:8]}", "avatar": ""}
    if platform == "xiaohongshu":
        try:
            with http_client("xiaohongshu") as c:
                r = c.get(f"https://www.xiaohongshu.com/user/profile/{uploader_id}")
            m = re.search(r'"nickname":"(.*?)"', r.text)
            a = re.search(r'"avatar":"(.*?)"', r.text)
            return {"name": (m.group(1) if m else f"博主{uploader_id[:8]}"),
                    "avatar": (a.group(1) if a else "")}
        except Exception:
            return {"name": f"博主{uploader_id[:8]}", "avatar": ""}
    if platform == "kuaishou":
        try:
            with http_client("kuaishou", mobile=True) as c:
                r = c.get(f"https://www.kuaishou.com/profile/{uploader_id}")
            m = re.search(r'"user_name":"(.*?)"', r.text) or \
                re.search(r'"userName":"(.*?)"', r.text)
            a = re.search(r'"headurl":"(.*?)"', r.text) or \
                re.search(r'"headUrl":"(.*?)"', r.text)
            return {"name": (m.group(1) if m else f"博主{uploader_id[:8]}"),
                    "avatar": (a.group(1) if a else "")}
        except Exception:
            return {"name": f"博主{uploader_id[:8]}", "avatar": ""}
    return {"name": uploader_id, "avatar": ""}


_WBI_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
            27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
            37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22,
            25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]


def _wbi_headers() -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126",
               "Referer": "https://www.bilibili.com/"}
    cookie = config.get("bilibili_cookie", "")
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _wbi_sign(params: dict) -> dict:
    """B站 web API wbi 签名（nav 接口取 key，按官方混淆表生成 w_rid）。"""
    import hashlib
    import time
    from urllib.parse import urlencode
    with httpx.Client(timeout=10, proxy=config.get("http_proxy") or None) as c:
        nav = c.get("https://api.bilibili.com/x/web-interface/nav",
                    headers=_wbi_headers()).json()
    wbi_img = ((nav.get("data") or {}).get("wbi_img") or {})
    img_key = (wbi_img.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = (wbi_img.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        raise RuntimeError("获取 wbi key 失败")
    orig = img_key + sub_key
    mixin = "".join(orig[i] for i in _WBI_TAB if i < len(orig))[:32]
    params = dict(sorted(params.items()))
    params["wts"] = int(time.time())
    params = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
              for k, v in params.items()}
    qs = urlencode(params)
    params["w_rid"] = hashlib.md5((qs + mixin).encode()).hexdigest()
    return params


def fetch_uploader_videos(platform: str, uploader_id: str,
                          limit: int = 20) -> list[dict]:
    """博主最新视频列表（订阅与主页批量下载共用）。"""
    if platform == "bilibili":
        return _fetch_bilibili_videos(uploader_id, limit)
    if platform == "douyin":
        return _fetch_douyin_videos(uploader_id, limit)
    if platform == "xiaohongshu":
        return _fetch_xiaohongshu_videos(uploader_id, limit)
    if platform == "kuaishou":
        return _fetch_kuaishou_videos(uploader_id, limit)
    raise ValueError("该平台暂不支持视频列表")


def _fetch_xiaohongshu_videos(user_id: str, limit: int = 20) -> list[dict]:
    """小红书博主笔记列表：主页 __INITIAL_STATE__（需 Cookie，实验性）。"""
    with http_client("xiaohongshu") as c:
        r = c.get(f"https://www.xiaohongshu.com/user/profile/{user_id}")
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if not m:
        raise RuntimeError("小红书主页数据未获取到（请先在设置中配置小红书 Cookie）")
    state = json.loads(m.group(1).replace("undefined", "null"))
    notes: list[dict] = []

    def dig(obj):
        if len(notes) >= limit:
            return
        if isinstance(obj, dict):
            if "noteId" in obj and ("title" in obj or "displayTitle" in obj):
                notes.append(obj)
                return
            for v in obj.values():
                dig(v)
        elif isinstance(obj, list):
            for v in obj:
                dig(v)

    dig(state)
    entries = []
    seen = set()
    for n in notes:
        nid = n.get("noteId") or ""
        if not nid or nid in seen:
            continue
        seen.add(nid)
        cover = n.get("cover") or {}
        entries.append({
            "video_id": nid,
            "title": (n.get("title") or n.get("displayTitle") or "无标题")[:80],
            "url": f"https://www.xiaohongshu.com/explore/{nid}",
            "cover": (cover.get("urlDefault") or cover.get("url") or "")
            if isinstance(cover, dict) else "",
            "publish_time": "",
        })
    return entries[:limit]


def _fetch_kuaishou_videos(user_id: str, limit: int = 20) -> list[dict]:
    """快手博主作品列表：主页 __NEXT_DATA__ 挖掘（实验性）。"""
    with http_client("kuaishou", mobile=True) as c:
        r = c.get(f"https://www.kuaishou.com/profile/{user_id}")
    if r.status_code != 200:
        raise RuntimeError(f"快手主页返回 {r.status_code}（可尝试配置快手 Cookie）")
    photos: list[dict] = []

    def dig(obj):
        if len(photos) >= limit:
            return
        if isinstance(obj, dict):
            if ("photoId" in obj or "photo_id" in obj) and "caption" in obj:
                photos.append(obj)
                return
            for v in obj.values():
                dig(v)
        elif isinstance(obj, list):
            for v in obj:
                dig(v)

    data = None
    for marker in ("__NEXT_DATA__", "videoData"):
        from .parsers.http_download import fetch_json_in_html
        data = fetch_json_in_html(r.text, f"{marker} =") or \
            fetch_json_in_html(r.text, f'"{marker}":')
        if isinstance(data, dict):
            break
    if not data:
        raise RuntimeError("快手主页数据未获取到（可尝试配置快手 Cookie）")
    dig(data)
    entries = []
    for p in photos[:limit]:
        pid = str(p.get("photoId") or p.get("photo_id") or "")
        if not pid:
            continue
        entries.append({
            "video_id": pid,
            "title": (p.get("caption") or "无标题").splitlines()[0][:80],
            "url": f"https://www.kuaishou.com/short-video/{pid}",
            "cover": (p.get("coverUrl") or "").split("!")[0],
            "publish_time": _ts((p.get("timestamp") or 0) / 1000
                                if p.get("timestamp", 0) > 10 ** 12 else p.get("timestamp")),
        })
    return entries


def _fetch_bilibili_videos(mid: str, limit: int = 20) -> list[dict]:
    """B站空间最新视频：wbi 签名直调 arc/search；412/失败时回退 yt-dlp。"""
    try:
        return _bili_videos_api(mid, limit)
    except Exception:
        return _bili_videos_ytdlp(mid, limit)


def _bili_videos_api(mid: str, limit: int) -> list[dict]:
    out, pn = [], 1
    with httpx.Client(timeout=15, proxy=config.get("http_proxy") or None) as c:
        while len(out) < limit and pn <= 2:
            params = _wbi_sign({"mid": mid, "ps": 30, "pn": pn})
            r = c.get("https://api.bilibili.com/x/space/wbi/arc/search",
                      params=params, headers=_wbi_headers())
            data = r.json()
            if data.get("code") not in (0, None):
                # -412 风控等：抛出让上层回退 yt-dlp
                raise RuntimeError(f"空间接口返回 code={data.get('code')}（{data.get('message') or '可能被风控'}）")
            vlist = ((data.get("data") or {}).get("list") or {}).get("vlist") or []
            if not vlist:
                break
            out.extend(vlist)
            pn += 1
    return [{
        "video_id": v["bvid"],
        "title": v.get("title") or "",
        "url": f"https://www.bilibili.com/video/{v['bvid']}",
        "cover": (v.get("pic") or "").replace("http://", "https://"),
        "publish_time": _ts(v.get("created")),
    } for v in out[:limit] if v.get("bvid")]


def _bili_videos_ytdlp(mid: str, limit: int) -> list[dict]:
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "extract_flat": True,
        "playlistend": limit, "socket_timeout": 20,
        "noplaylist": False,
    }
    proxy = config.get("http_proxy") or None
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://space.bilibili.com/{mid}/video",
                                download=False)
    out = []
    for e in (info.get("entries") or [])[:limit]:
        bvid = e.get("id") or ""
        if bvid:
            out.append({"video_id": bvid, "title": e.get("title") or bvid,
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "cover": e.get("thumbnail") or "",
                        "publish_time": ""})
    return out


def _fetch_douyin_videos(sec_uid: str, limit: int = 20) -> list[dict]:
    """抖音博主最新视频：用户主页 _ROUTER_DATA（尽力而为，风控时抛错）。"""
    with http_client("douyin", mobile=True) as c:
        r = c.get(f"https://www.douyin.com/user/{sec_uid}")
    from .parsers.http_download import fetch_json_in_html
    data = fetch_json_in_html(r.text, "_ROUTER_DATA")
    items: list[dict] = []

    def dig(obj):
        if isinstance(obj, dict):
            if "aweme_id" in obj and ("desc" in obj or "video" in obj):
                items.append(obj)
                return
            for v in obj.values():
                dig(v)
        elif isinstance(obj, list):
            for v in obj:
                dig(v)

    if not data:
        raise RuntimeError("抖音主页数据未获取到（可能需要配置 Cookie 或触发风控）")
    dig(data)
    entries = []
    for it in items[:limit]:
        if not it.get("aweme_id"):
            continue
        cover = ((it.get("video") or {}).get("cover") or {}).get("url_list") or [""]
        entries.append({
            "video_id": str(it.get("aweme_id") or ""),
            "title": (it.get("desc") or "无标题").splitlines()[0][:80],
            "url": f"https://www.douyin.com/video/{it.get('aweme_id')}",
            "cover": cover[0] if cover else "",
            "publish_time": _ts(it.get("create_time")),
        })
    return entries


def _ts(t) -> str:
    if not t:
        return ""
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return ""


def check_subscription(sub: dict) -> int:
    """检查一个订阅，新视频入队。返回新增数。"""
    try:
        videos = fetch_uploader_videos(sub["platform"], sub["uploader_id"])
    except Exception as e:
        database.update_sub(sub["id"], last_error=str(e)[:300],
                            last_checked=database.now())
        raise

    # 每订阅的覆盖选项（未设置时用全局默认）
    try:
        sub_opts = json.loads(sub.get("options") or "{}")
    except json.JSONDecodeError:
        sub_opts = {}

    new_count = 0
    for v in videos:
        if not v["video_id"]:
            continue
        if database.find_by_video_id(sub["platform"], v["video_id"]):
            continue
        try:
            options = {
                "quality": config.get("default_quality", "best"),
                "extract_audio": sub_opts.get("extract_audio",
                                             bool(config.get("extract_audio", False))),
                "download_danmaku": sub_opts.get("download_danmaku",
                                                 bool(config.get("download_danmaku", False))),
                "from_subscription": sub["id"],
            }
            manager.create_task(v["url"], options, force=False)
            new_count += 1
        except Exception:
            continue  # 单个视频失败不影响整批

    database.update_sub(
        sub["id"], last_checked=database.now(), last_error="",
        new_count=sub["new_count"] + new_count)
    if new_count:
        database.insert_notification(
            "subscription",
            f"「{sub['uploader_name']}」更新 {new_count} 个视频",
            "已自动加入下载队列")
    return new_count


class SubscriptionScheduler:
    def __init__(self):
        self._sched: BackgroundScheduler | None = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._sched and self._sched.running:
                return
            minutes = max(5, int(config.get("subscription_interval", 30)))
            self._sched = BackgroundScheduler(daemon=True)
            self._sched.add_job(self.check_all, "interval", minutes=minutes,
                                id="subs_check", max_instances=1, coalesce=True)
            self._sched.start()

    def check_all(self):
        for sub in database.list_subs():
            if not sub["enabled"]:
                continue
            try:
                check_subscription(sub)
            except Exception:
                pass  # 错误已写入 last_error

    def restart(self):
        """配置变更后重建调度。"""
        if self._sched and self._sched.running:
            self._sched.shutdown(wait=False)
            self._sched = None
        self.start()


sub_scheduler = SubscriptionScheduler()
