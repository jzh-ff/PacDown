"""PacDown 启动入口：python run.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app import config


def main():
    cfg = config.load()
    port = int(cfg.get("port", 8300))
    host = os.environ.get("PACDOWN_HOST", "127.0.0.1")
    print(f"""
  ╔══════════════════════════════════════╗
  ║   PacDown · 全平台视频下载工具        ║
  ╚══════════════════════════════════════╝
  本地访问:  http://{host}:{port}
  下载目录:  {cfg.get('download_dir')}
  按 Ctrl+C 停止
""")
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
