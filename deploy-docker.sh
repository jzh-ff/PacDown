#!/bin/bash
# PacDown 服务器端 Docker 发布脚本（本机通过 ssh "bash -s -- <目录>" 触发，也可手动执行）。
# 流程：解包 → docker 构建镜像（国内镜像加速）→ 容器切换 → 健康检查（失败自动回滚镜像）。
set -euo pipefail

APP_DIR="${1:-/www/wwwroot/pacdown}"
CONTAINER="pacdown"
IMAGE="pacdown"
PORT="8300"
HEALTH_URL="http://127.0.0.1:${PORT}/api/platforms"

# 国内加速（可用环境变量覆盖）：腾讯云内网 apt 镜像 + 清华 PyPI
export APT_MIRROR="${APT_MIRROR:-mirrors.cloud.tencent.com}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

cd "$APP_DIR"

echo "==> [1/6] 检查上传的运行包"
TGZ="$APP_DIR/pacdown.tgz"
if [ ! -f "$TGZ" ]; then
  echo "✗ 未找到 pacdown.tgz（先在本机执行 scripts/deploy-local.sh 或 deploy.ps1）"
  exit 1
fi

echo "==> [2/6] 检查 Docker 环境"
if ! command -v docker >/dev/null 2>&1; then
  echo "✗ 未安装 Docker。请先执行：curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "✗ 未安装 docker compose 插件（docker-compose-plugin）"
  exit 1
fi

echo "==> [3/6] 解压运行包到应用目录"
tar xzf "$TGZ" -C "$APP_DIR" --strip-components=1
rm -f "$TGZ"
mkdir -p "$APP_DIR/downloads" "$APP_DIR/data" "$APP_DIR/config"

echo "==> [4/6] 备份当前镜像并构建新镜像"
docker tag "$IMAGE:latest" "$IMAGE:backup" 2>/dev/null || true
docker compose build

echo "==> [5/6] 切换容器"
docker compose up -d

echo "==> [6/6] 健康检查（最多等 60 秒）"
ok=""
for i in $(seq 1 12); do
  sleep 5
  if curl -sf -o /dev/null --max-time 10 "$HEALTH_URL"; then
    ok=1
    break
  fi
  echo "    等待容器就绪… ($((i * 5))s)"
done
if [ -n "$ok" ]; then
  echo "✓ 发布成功"
  docker image prune -f >/dev/null 2>&1 || true
  echo "  （回滚命令：docker tag $IMAGE:backup $IMAGE:latest && docker compose up -d）"
else
  echo "✗ 健康检查失败，自动回滚镜像…"
  if docker image inspect "$IMAGE:backup" >/dev/null 2>&1; then
    docker tag "$IMAGE:backup" "$IMAGE:latest"
    docker compose up -d
    sleep 5
    curl -sf -o /dev/null --max-time 10 "$HEALTH_URL" \
      && echo "✓ 已回滚到旧版本" \
      || echo "✗✗ 回滚后仍异常，请人工检查：docker logs $CONTAINER"
  else
    echo "✗✗ 无备份镜像可回滚，请人工检查：docker logs $CONTAINER"
  fi
  exit 1
fi
