"""PacDown 启动入口：python run.py（源码）或 PacDown.exe（PyInstaller 打包）"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app import config
from app.main import app as asgi_app  # 直接导入：PyInstaller 才能把全部路由打进 exe


def main():
    cfg = config.load()
    port = int(cfg.get("port", 8300))
    host = os.environ.get("PACDOWN_HOST", "127.0.0.1")
    frozen = getattr(sys, "frozen", False)
    print(f"""
  ╔══════════════════════════════════════╗
  ║   PacDown · 全平台视频下载工具        ║
  ╚══════════════════════════════════════╝
  本地访问:  http://{host}:{port}
  下载目录:  {cfg.get('download_dir')}
  按 Ctrl+C 退出
""")
    # 打包版：启动后自动打开浏览器（PACDOWN_NO_BROWSER=1 可禁用）
    if frozen and not os.environ.get("PACDOWN_NO_BROWSER"):
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        print("  正在自动打开浏览器… 若未打开请手动访问上面的地址")
    uvicorn.run(asgi_app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
