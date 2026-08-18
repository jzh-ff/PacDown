#!/usr/bin/env bash
# 本机发布脚本（macOS / Linux / Windows git-bash）。
# 用法：./scripts/deploy-local.sh
# 可用环境变量覆盖目标：DEPLOY_HOST=root@82.156.224.145 DEPLOY_DIR=/www/wwwroot/pacdown
# Python 无需构建：git archive 打包源码即运行包，服务器软链切换 + 自动回滚。
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${DEPLOY_HOST:-root@82.156.224.145}"
DIR="${DEPLOY_DIR:-/www/wwwroot/pacdown}"

echo "==> [1/4] 打包运行包（git archive，仅含已跟踪文件）"
rm -f pacdown.tgz
git archive --format=tar.gz --prefix=pacdown/ -o pacdown.tgz HEAD
du -sh pacdown.tgz

echo "==> [2/4] 确保服务器目录存在"
ssh "$HOST" "mkdir -p $DIR"

echo "==> [3/4] 上传到 $HOST"
scp pacdown.tgz "$HOST:$DIR/"

echo "==> [4/4] 触发服务器切换"
ssh "$HOST" "bash $DIR/deploy.sh"

rm -f pacdown.tgz
echo "✓ 发布完成"
