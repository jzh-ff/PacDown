"""解析器包：import 时触发各平台注册。"""
from . import bilibili, douyin, kuaishou, xiaohongshu, generic  # noqa: F401
from .base import (ParseError, Parser, VideoInfo, all_parsers,
                   dispatch, extract_urls, guess_platform)

PLATFORM_META = {
    "bilibili": {"name": "哔哩哔哩", "color": "#fb7299"},
    "douyin": {"name": "抖音", "color": "#fe2c55"},
    "kuaishou": {"name": "快手", "color": "#ff7900"},
    "xiaohongshu": {"name": "小红书", "color": "#ff2442"},
    "generic": {"name": "通用", "color": "#6366f1"},
}
