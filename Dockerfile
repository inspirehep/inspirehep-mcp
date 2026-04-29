FROM python:3.12-slim AS base

RUN pip install poetry
ENV POETRY_VIRTUALENVS_IN_PROJECT=true

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry install --without dev --no-interaction

COPY server.py .

FROM base AS test
RUN poetry install --no-interaction
COPY tests/ app/tests/.

FROM base AS prod
EXPOSE 8000
CMD ["poetry", "run", "python", "server.py", "--transport", "http", "--port", "8000"]
