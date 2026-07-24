FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12

WORKDIR /code

COPY --from=uv /uv /uvx /bin/

COPY ./pyproject.toml ./uv.lock /code/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN uv sync --locked --no-dev --no-install-project

COPY ./app /code/app
COPY ./public.pem /code/public.pem

# If running behind a proxy like Nginx or Traefik add --proxy-headers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
