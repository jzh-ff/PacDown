@echo off
chcp 65001 >nul
title PacDown - 全平台视频下载工具
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH
    pause
    exit /b 1
)

if not exist ".venv" (
    echo 首次运行：创建虚拟环境并安装依赖...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    call .venv\Scripts\activate.bat
)

echo 启动 PacDown...
python run.py
pause
