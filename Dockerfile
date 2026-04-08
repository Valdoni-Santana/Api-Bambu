# Microserviço bambu-bridge — Python 3.11
FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/app_pkg

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY requirements-bridge.txt /app/requirements-bridge.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-bridge.txt

COPY bambulab /app/bambulab
COPY app /app/app_pkg

RUN mkdir -p /app/storage/snapshots

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://127.0.0.1:8010/health || exit 1

CMD ["uvicorn", "bridge.main:app", "--host", "0.0.0.0", "--port", "8010"]
