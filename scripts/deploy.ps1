# 本机发布脚本（Windows PowerShell）。
# 用法：在项目根目录执行  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 [docker|pm2]  （默认 docker）
# Python 无需构建：git archive 打包源码即运行包，服务器切换 + 自动回滚。
param([string]$Mode = "docker")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$DeployHost = if ($env:DEPLOY_HOST) { $env:DEPLOY_HOST } else { "root@82.156.224.145" }
$DeployDir  = if ($env:DEPLOY_DIR)  { $env:DEPLOY_DIR }  else { "/www/wwwroot/pacdown" }
$ServerScript = switch ($Mode) {
  "docker" { "deploy-docker.sh" }
  "pm2"    { "deploy.sh" }
  default  { throw "未知模式：$Mode（可选 docker | pm2）" }
}

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

Write-Host "==> [4/4] 触发服务器切换（模式：$Mode → $ServerScript）"
# 通过 stdin 把服务器端脚本推送执行：首次部署服务器上还没有脚本，且始终用本机最新版本
Get-Content $ServerScript -Raw | ssh $DeployHost "bash -s -- '$DeployDir'"
if ($LASTEXITCODE -ne 0) { throw "服务器发布失败" }

Remove-Item pacdown.tgz
Write-Host "✓ 发布完成"
