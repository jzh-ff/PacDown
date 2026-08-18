@echo off
rem Build portable Windows PacDown.exe (config/downloads/data live next to the exe)
rem Output: dist-app\PacDown.exe
setlocal
cd /d "%~dp0.."

echo ==^> [1/3] Install pyinstaller
python -m pip install -q pyinstaller || goto :fail

echo ==^> [2/3] Generate icon if missing
if not exist assets\icon.ico python scripts\make_icon.py || goto :fail

echo ==^> [3/3] PyInstaller build
python -m PyInstaller --noconfirm --clean --onefile --name PacDown ^
  --icon assets\icon.ico ^
  --add-data "static;static" ^
  --hidden-import multipart.multipart ^
  --collect-submodules uvicorn ^
  run.py || goto :fail

if not exist dist-app mkdir dist-app
move /y dist\PacDown.exe dist-app\PacDown.exe >nul
for %%F in (dist-app\PacDown.exe) do echo DONE: dist-app\PacDown.exe ^(%%~zF bytes^)
echo Upload: scp dist-app\PacDown.exe root@82.156.224.145:/www/wwwroot/pacdown/appdist/
goto :eof

:fail
echo BUILD FAILED
exit /b 1
