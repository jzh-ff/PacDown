FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY run.py .

ENV PACDOWN_HOST=0.0.0.0
ENV PACDOWN_CONFIG_DIR=/app/config

EXPOSE 8300

VOLUME ["/app/downloads", "/app/data", "/app/config"]

CMD ["python", "run.py"]
