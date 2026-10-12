FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app
# Dependencies first: this layer is rebuilt only when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
# settings.py refuses DEBUG=0 without a key; this one exists only during the build.
RUN DEBUG=0 SECRET_KEY=collectstatic python manage.py collectstatic --noinput

# No home directory: gunicorn's control socket (unused here) would want one, hence --no-control-socket below.
RUN useradd --system --no-create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi --bind 0.0.0.0:8000 -k gthread --workers 3 --threads 4 --timeout 30 --access-logfile - --no-control-socket"]
