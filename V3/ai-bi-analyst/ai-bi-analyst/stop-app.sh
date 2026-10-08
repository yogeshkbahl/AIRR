#!/usr/bin/env bash
# Stops AI BI Analyst. Uploaded datasets and logs are kept.
cd "$(dirname "$0")"
docker compose -f docker-compose.app.yml down
echo "AI BI Analyst stopped."
