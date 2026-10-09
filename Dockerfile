FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 zlib1g ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    HIKSDK_PATH=/opt/hiksdk \
    DATA_DIR=/app/data \
    APP_PORT=8123 \
    LD_LIBRARY_PATH=/opt/hiksdk

EXPOSE 8123
CMD ["python", "-m", "app"]
