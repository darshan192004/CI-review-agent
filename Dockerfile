FROM python:3.13-slim AS builder

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

COPY . .
RUN pip install --no-cache-dir --prefix=/install .


# -- Runtime stage -----------------------------------------------------------
FROM python:3.13-slim AS runtime

LABEL maintainer="ci-review-agent"
LABEL description="Self-healing CI/CD agent using LangGraph"

WORKDIR /app

COPY --from=builder /install /usr/local

COPY . .

RUN adduser --disabled-password --no-create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENV SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8000/health'); r.raise_for_status()"

ENTRYPOINT ["python", "-m", "main"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
