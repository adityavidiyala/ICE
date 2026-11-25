FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends bash git nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home ice \
    && mkdir /workspace && chown 10001:10001 /workspace
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY sandbox/ ./sandbox/
COPY tools/ ./tools/
COPY safety/ ./safety/
COPY ui/ ./ui/
COPY utils/ ./utils/
COPY agent/ ./agent/
COPY config.py ./
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /workspace
CMD ["sleep", "infinity"]
