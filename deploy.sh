#!/bin/bash
# PacDown 服务器端发布脚本（由本机 deploy-local 触发，也可手动执行）。
# 流程：解包 → 依赖检查 → 目录软链原子切换 → PM2 重启 → 健康检查（失败自动回滚）。
# 服务器只做轻量操作；Python 无需构建，包即源码。
set -euo pipefail

APP_DIR="/www/wwwroot/pacdown"
PM2_NAME="pacdown"
PORT="8300"
HEALTH_URL="http://127.0.0.1:${PORT}/api/platforms"
KEEP_RELEASES=3
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
cd "$APP_DIR"

echo "==> [1/7] 检查上传的运行包"
TGZ="$APP_DIR/pacdown.tgz"
if [ ! -f "$TGZ" ]; then
  echo "✗ 未找到 pacdown.tgz（先在本机执行 scripts/deploy-local.sh 或 deploy.ps1）"
  exit 1
fi

echo "==> [2/7] 解压到新版本目录"
REL="$APP_DIR/releases/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$REL"
tar xzf "$TGZ" -C "$REL" --strip-components=1
rm -f "$TGZ"

echo "==> [3/7] Python 环境与依赖"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "    首次部署，创建 venv…"
  python3 -m venv "$APP_DIR/.venv"
fi
REQ_NEW=$(md5sum "$REL/requirements.txt" | cut -d' ' -f1)
REQ_OLD=$(md5sum "$APP_DIR/current/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo "")
if [ "$REQ_NEW" != "$REQ_OLD" ]; then
  echo "    requirements.txt 有变化，安装依赖…"
  "$APP_DIR/.venv/bin/pip" install -q -i "$PIP_INDEX" -r "$REL/requirements.txt"
else
  echo "    依赖无变化，跳过"
fi

echo "==> [4/7] 准备持久化目录 shared/（config/downloads/data 跨版本保留）"
mkdir -p "$APP_DIR/shared/downloads" "$APP_DIR/shared/data"
if [ ! -f "$APP_DIR/shared/config.json" ]; then
  echo "    首次部署，生成默认配置"
  cat > "$APP_DIR/shared/config.json" <<EOF
{"download_dir": "$APP_DIR/shared/downloads"}
EOF
fi

echo "==> [5/7] 原子切换 current 软链"
[ -d "$APP_DIR/current.old" ] && rm -rf "$APP_DIR/current.old"
[ -L "$APP_DIR/current" ] && cp -P "$APP_DIR/current" "$APP_DIR/current.old" || true
ln -sfn "$REL" "$APP_DIR/current"

echo "==> [6/7] 重启 PM2"
cat > "$APP_DIR/ecosystem.config.js" <<EOF
module.exports = { apps: [{
  name: "$PM2_NAME",
  script: "run.py",
  interpreter: "$APP_DIR/.venv/bin/python",
  cwd: "$APP_DIR/current",
  env: {
    PACDOWN_HOST: "0.0.0.0",
    PACDOWN_CONFIG_DIR: "$APP_DIR/shared"
  }
}]}
EOF
if pm2 describe "$PM2_NAME" >/dev/null 2>&1; then
  pm2 restart "$PM2_NAME" >/dev/null
else
  pm2 start "$APP_DIR/ecosystem.config.js" >/dev/null
  pm2 save >/dev/null
fi

echo "==> [7/7] 健康检查"
sleep 4
if curl -sf -o /dev/null --max-time 10 "$HEALTH_URL"; then
  echo "✓ 发布成功"
  # 清理旧版本，只保留最近 KEEP_RELEASES 个
  cd "$APP_DIR/releases"
  ls -1t | tail -n +$((KEEP_RELEASES + 1)) | xargs -r rm -rf
  echo "  （旧版本保留最近 $KEEP_RELEASES 个，可手动回滚：ln -sfn releases/<旧版本> $APP_DIR/current && pm2 restart $PM2_NAME）"
else
  echo "✗ 健康检查失败，自动回滚…"
  if [ -L "$APP_DIR/current.old" ]; then
    OLD_TARGET=$(readlink "$APP_DIR/current.old")
    ln -sfn "$OLD_TARGET" "$APP_DIR/current"
    pm2 restart "$PM2_NAME" >/dev/null
    sleep 4
    curl -sf -o /dev/null --max-time 10 "$HEALTH_URL" \
      && echo "✓ 已回滚到旧版本" \
      || echo "✗✗ 回滚后仍异常，请人工检查 pm2 logs $PM2_NAME"
  else
    echo "✗✗ 无旧版本可回滚，请人工检查 pm2 logs $PM2_NAME"
  fi
  exit 1
fi
