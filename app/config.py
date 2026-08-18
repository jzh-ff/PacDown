"""配置管理：读取/保存 config.json，提供默认值。"""
import json
import os
import sys
import threading
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
# PyInstaller 打包后：exe 所在目录即数据目录（便携模式，config/downloads 都在 exe 旁边）
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
# Docker 部署时配置可挂载到独立目录（docker-compose 中挂载 /app/config）
CONFIG_DIR = Path(os.environ.get("PACDOWN_CONFIG_DIR", str(ROOT_DIR)))
CONFIG_PATH = CONFIG_DIR / "config.json"

_lock = threading.Lock()

DEFAULTS = {
    "download_dir": str(ROOT_DIR / "downloads"),
    "recent_dirs": [],              # 最近使用过的下载目录
    "max_concurrency": 3,           # 同时下载任务数
    "bilibili_cookie": "",          # SESSDATA=xxx; buvid3=xxx 形式
    "douyin_cookie": "",
    "kuaishou_cookie": "",
    "xiaohongshu_cookie": "",
    "http_proxy": "",               # 例 http://127.0.0.1:7890
    "speed_limit_mb": 0,            # 全局限速 MB/s（0 不限；单任务=全局/并发数）
    "admin_key": "",                # 管理面板密钥（空=统计面板隐藏）
    "name_template": "{date}_{title}",          # 文件名模板（目录固定 平台/作者/）
    "default_quality": "best",      # best 或具体清晰度 id
    "extract_audio": False,         # 默认是否提取 MP3
    "download_danmaku": False,      # B站默认是否下载弹幕
    "subscription_interval": 30,    # 订阅检查间隔（分钟）
    "auto_clean_enabled": False,    # 自动清理：删除超过保留天数的已完成内容
    "auto_clean_days": 30,          # 保留天数
    "auto_clean_keep_favorite": True,  # 收藏的内容不清理
    "ai_base_url": "https://open.bigmodel.cn/api/paas/v4",  # OpenAI 兼容端点
    "ai_api_key": "",
    "ai_model": "glm-4-flash",
    "port": 8300,
    "theme": "dark",
}

_config: dict = {}


def load() -> dict:
    global _config
    with _lock:
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                saved = {}
        else:
            saved = {}
        _config = {**DEFAULTS, **saved}
        return _config


def get(key: str, default=None):
    if not _config:
        load()
    return _config.get(key, DEFAULTS.get(key, default))


def all_settings() -> dict:
    if not _config:
        load()
    return dict(_config)


def update(patch: dict) -> dict:
    """合并更新配置并落盘。敏感字段不回显由 API 层处理。"""
    global _config
    with _lock:
        if not _config:
            _config = dict(DEFAULTS)
        # 端口等运行期字段不允许通过此接口改
        for key in ("port",):
            patch.pop(key, None)
        _config.update(patch)
        # 维护最近目录列表
        if "download_dir" in patch:
            dirs = _config.setdefault("recent_dirs", [])
            d = patch["download_dir"]
            if d in dirs:
                dirs.remove(d)
            dirs.insert(0, d)
            del dirs[5:]
        CONFIG_PATH.write_text(json.dumps(_config, ensure_ascii=False, indent=2), "utf-8")
        return dict(_config)


def set_download_dir(path_str: str) -> dict:
    """切换下载目录：不存在则创建，写入当前目录并记录历史。"""
    p = Path(path_str)
    p.mkdir(parents=True, exist_ok=True)
    return update({"download_dir": str(p.resolve())})


def load_once_at_startup() -> None:
    load()
