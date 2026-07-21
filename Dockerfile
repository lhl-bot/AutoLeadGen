ARG PYTHON_IMAGE=python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements.txt ./
RUN pip install --upgrade pip==25.0.1 && pip install --requirement requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PORT=8001
WORKDIR /app
RUN groupadd --gid 10001 autoleadgen \
    && useradd --uid 10001 --gid autoleadgen --no-create-home --shell /usr/sbin/nologin autoleadgen
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=3).read()"]
CMD ["gunicorn", "main:app", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "1", "--bind", "0.0.0.0:8001", "--forwarded-allow-ips", "*", "--timeout", "120", "--graceful-timeout", "30", "--keep-alive", "5", "--max-requests", "2000", "--max-requests-jitter", "200", "--access-logfile", "-", "--error-logfile", "-"]
