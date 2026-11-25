ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv
FROM ${PYTHON_IMAGE} AS build
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    IMOB_APP_ROOT=/app \
    IMOB_MAPS_DIR=/var/lib/imob/maps \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=build /app/.venv ./.venv
COPY alembic.ini ./
COPY migrations/env.py ./migrations/
COPY migrations/mysql_versions ./migrations/mysql_versions
RUN groupadd --gid 10001 imob \
 && useradd --no-create-home --uid 10001 --gid 10001 imob \
 && install -d -o 10001 -g 10001 /var/lib/imob/maps \
 && install -o 10001 -g 10001 /dev/null /var/lib/imob/maps/.volume-init
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).close()"]
CMD ["imob", "serve", "--host", "0.0.0.0", "--port", "8000"]
