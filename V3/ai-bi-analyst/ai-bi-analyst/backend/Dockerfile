FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKSPACE_DIR=/data/workspace \
    METADATA_DB=/data/metadata.sqlite

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY samples ./samples

# Analysis sessions are isolated per dataset id under this volume.
RUN mkdir -p /data/workspace && useradd -m analyst && chown -R analyst /data /app
USER analyst

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
