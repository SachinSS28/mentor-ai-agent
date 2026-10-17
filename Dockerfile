# Mentor's server image: the Telegram bot now; api/worker/sentinel use it as they're built.
# Runs on the Google Cloud server (plan D20) and later on AWS. Built by infra/gcp/deploy.sh.
FROM ghcr.io/astral-sh/uv:0.12.24 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY backend backend
COPY config config
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 mentor
USER mentor
ENV PATH=/app/.venv/bin:$PATH
ENTRYPOINT ["mentor"]
CMD ["telegram"]
