# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.7.20 AS uv

FROM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system baba && useradd --system --gid baba --home /app baba

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY manage.py ./
COPY src ./src
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# collectstatic needs settings but no real secrets or database.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

USER baba
EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
# Gunicorn reads the worker count from WEB_CONCURRENCY.
ENV WEB_CONCURRENCY=3
CMD ["gunicorn", "baba.wsgi:application", "--chdir", "src", "--bind", "0.0.0.0:8000", "--no-control-socket", "--access-logfile", "-"]
