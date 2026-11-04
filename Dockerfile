FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    PORT=8000 \
    KEEPALIVE_URL=https://cred-manager.blitz48.blitz.cloud/api/health \
    KEEPALIVE_INTERVAL=180

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY cli /app/cli
COPY scripts /app/scripts
RUN chmod +x /app/cli/install.sh /app/cli/cred-cli.py /app/cli/bin/* /app/scripts/keepalive.py /app/scripts/keepalive.sh || true && \
    mkdir -p /app/data

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["sh", "-c", "python /app/scripts/keepalive.py --url ${KEEPALIVE_URL} --interval ${KEEPALIVE_INTERVAL} & exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
