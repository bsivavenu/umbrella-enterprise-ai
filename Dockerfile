# Umbrella AI container image.
#
# Two stages: the first installs the locked dependencies with uv, the second copies only the
# finished environment and the app code, so the final image has no build tools.
#
# No secrets are baked in: settings come from environment variables at run time
# (see .env.example for the names). .dockerignore keeps .env out of the build.

# ---- stage 1: install dependencies exactly as locked in uv.lock ----
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- stage 2: runtime image ----
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/srv/.venv/bin:$PATH"
WORKDIR /srv
RUN useradd -r -u 10001 umbrella
COPY --from=build /srv/.venv /srv/.venv
COPY app ./app
COPY worker ./worker
# kept so the database tables can be created from the container:
#   docker run --rm --env-file .env <image> python scripts/init_db.py
COPY scripts ./scripts
COPY infra ./infra
USER umbrella

# Hosting platforms such as Render set $PORT; 8000 is the default.
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health/live', timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
