# 本机发布脚本（Windows PowerShell）。
# 用法：在项目根目录执行  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
# Python 无需构建：git archive 打包源码即运行包，服务器软链切换 + 自动回滚。
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$DeployHost = if ($env:DEPLOY_HOST) { $env:DEPLOY_HOST } else { "root@82.156.224.145" }
$DeployDir  = if ($env:DEPLOY_DIR)  { $env:DEPLOY_DIR }  else { "/www/wwwroot/pacdown" }

Write-Host "==> [1/4] 打包运行包（git archive，仅含已跟踪文件）"
if (Test-Path pacdown.tgz) { Remove-Item pacdown.tgz }
git archive --format=tar.gz --prefix=pacdown/ -o pacdown.tgz HEAD
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

Write-Host "==> [2/4] 确保服务器目录存在"
ssh $DeployHost "mkdir -p $DeployDir"
if ($LASTEXITCODE -ne 0) { throw "SSH 连接失败" }

Write-Host "==> [3/4] 上传到 $DeployHost"
scp pacdown.tgz "${DeployHost}:${DeployDir}/"
if ($LASTEXITCODE -ne 0) { throw "上传失败" }

Write-Host "==> [4/4] 触发服务器切换"
ssh $DeployHost "bash $DeployDir/deploy.sh"

Remove-Item pacdown.tgz
Write-Host "✓ 发布完成"
