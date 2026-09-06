# Slim API image — no PyTorch, no FFmpeg. edge-tts is a light SDK for voice listing only.

FROM python:3.12-slim AS builder

WORKDIR /src
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[edge]"

FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
