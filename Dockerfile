FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt ./
RUN pip install --upgrade pip && pip wheel --wheel-dir /build/wheels -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    netcat-openbsd \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
COPY --from=builder /build/wheels /wheels
RUN pip install --upgrade pip && pip install --no-index --find-links=/wheels -r /app/requirements.txt && rm -rf /wheels

COPY . .

RUN chmod +x /app/docker/entrypoint.sh

# Run as non-root user for security.
RUN groupadd --gid 1000 crawler && \
    useradd --uid 1000 --gid crawler --no-create-home crawler && \
    chown -R crawler:crawler /app
USER crawler

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["scrapy", "crawl", "discovery", "-a", "max_pages=10"]
