FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app
COPY . .
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN uv sync --frozen --no-dev
RUN mkdir -p /data/staging
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" || exit 1
CMD ["uv", "run", "--frozen", "--no-dev", "fuwu-server"]
