"""解析器抽象：VideoInfo 统一数据模型 + Parser 注册机制。

每个平台一个 Parser 子类，@register 注册后由 dispatch(url) 按 can_parse 匹配；
未匹配任何专门解析器时回落到 generic（yt-dlp 引擎，覆盖上千站点）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


class ParseError(Exception):
    """解析失败，message 直接展示给用户。"""


@dataclass
class VideoInfo:
    platform: str
    video_id: str = ""
    source_url: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    avatar_url: str = ""
    cover_url: str = ""
    duration: int = 0                    # 秒
    publish_time: str = ""
    description: str = ""                # 作者发布的完整文案
    stats: dict = field(default_factory=dict)      # 播放/点赞/收藏等
    quality_options: list = field(default_factory=list)  # [{id,label}]
    images: list = field(default_factory=list)     # 图集 URL 列表
    is_images: bool = False              # 是否图集类型
    raw: dict = field(default_factory=dict)       # 平台原始数据，写 sidecar


# URL 提取：从任意分享文本中抓出全部 http(s) 链接
URL_RE = re.compile(r"https?://[^\s，,。、！!？?\)）\"'<>]+")

KNOWN_HOSTS = [
    ("bilibili", ["bilibili.com", "b23.tv"]),
    ("douyin", ["douyin.com", "iesdouyin.com"]),
    ("kuaishou", ["kuaishou.com", "chenzhongtech.com", "gifshow.com"]),
    ("xiaohongshu", ["xiaohongshu.com", "xhslink.com"]),
]


def extract_urls(text: str) -> list[str]:
    """从粘贴文本中提取所有链接（每行一个链接、或整段分享文案均可）。"""
    return [u.rstrip("\\").rstrip("】，。") for u in URL_RE.findall(text or "")]


def guess_platform(url: str) -> str | None:
    low = url.lower()
    for name, hosts in KNOWN_HOSTS:
        if any(h in low for h in hosts):
            return name
    return None


class Parser:
    """平台解析器基类。子类需实现 can_parse / parse / download。"""

    platform: str = ""
    display_name: str = ""

    def can_parse(self, url: str) -> bool:  # pragma: no cover - 子类实现
        return False

    def parse(self, url: str) -> VideoInfo:  # pragma: no cover
        raise ParseError("解析器未实现")

    def download(self, info: VideoInfo, dest_dir: str, options: dict,
                 progress: Callable[[float, str], None]) -> dict:
        """下载到 dest_dir，返回 {file_path, file_size, cover_path, images:[...]}。

        progress(百分比0-100, 速度描述) 由实现周期性回调。
        """
        raise ParseError("下载器未实现")


_REGISTRY: list[Parser] = []


def register(cls):
    inst = cls()
    _REGISTRY.append(inst)
    return cls


def all_parsers() -> list[Parser]:
    return list(_REGISTRY)


def dispatch(url: str) -> Parser:
    """按注册顺序匹配专门解析器；都不命中则返回 generic。"""
    from . import generic
    for p in _REGISTRY:
        if p.platform != "generic" and p.can_parse(url):
            return p
    return generic.GenericParser()
