# Project Silver: dashboard + collector in one container. See "Self-hosting" in README.md.
FROM ghcr.io/astral-sh/uv:0.13.4-python3.12-trixie-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev
COPY README.md ./
COPY silver ./silver
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 1000 silver && mkdir /data && chown silver /data
USER silver
ENV SILVER_DATA=/data PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=1m --timeout=5s --start-period=2m \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/config', timeout=4)"
ENTRYPOINT ["silver"]
CMD ["serve", "--host", "0.0.0.0"]
