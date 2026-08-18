FROM python:3.12-slim

# 可选构建参数：国内服务器加速（deploy-docker.sh 默认已注入腾讯云内网镜像）
ARG APT_MIRROR=""
ARG PIP_INDEX_URL="https://pypi.org/simple"

# apt 源替换（bookworm 起为 deb822 格式）
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY app ./app
COPY static ./static
COPY run.py .

ENV PACDOWN_HOST=0.0.0.0
ENV PACDOWN_CONFIG_DIR=/app/config

EXPOSE 8300

VOLUME ["/app/downloads", "/app/data", "/app/config"]

CMD ["python", "run.py"]
