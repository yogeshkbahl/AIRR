#!/usr/bin/env bash
# Starts AI BI Analyst in Docker and opens it in the browser (macOS / Linux).
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker is not installed: https://www.docker.com/products/docker-desktop/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and try again."; exit 1; }

# First run on this machine: create .env from answers.
if [ ! -f .env ]; then
  echo "First run: choose the AI provider (keys stay on this machine)."
  echo "  1) Anthropic Claude   2) OpenAI   3) None (offline, rule-based)"
  read -r -p "Choice [1]: " choice
  case "${choice:-1}" in
    2) provider=openai;    key_name=OPENAI_API_KEY ;;
    3) provider=heuristic; key_name="" ;;
    *) provider=anthropic; key_name=ANTHROPIC_API_KEY ;;
  esac
  { echo "LLM_PROVIDER=$provider"; echo "APP_PORT=8080"; } > .env
  if [ -n "$key_name" ]; then
    read -r -s -p "Paste your $key_name: " key; echo
    echo "$key_name=$key" >> .env
  fi
  chmod 600 .env
fi

port=$(sed -n 's/^[[:space:]]*APP_PORT[[:space:]]*=[[:space:]]*\([0-9]*\).*/\1/p' .env | head -1)
url="http://localhost:${port:-8080}"

# Containers default to UTC; pass the host zone so log folders match the local day.
if [ -z "${TZ:-}" ]; then
  TZ=$(readlink /etc/localtime 2>/dev/null | sed -n 's|.*zoneinfo/||p')
  [ -z "$TZ" ] && [ -f /etc/timezone ] && TZ=$(cat /etc/timezone)
  export TZ="${TZ:-UTC}"
fi

# The backend runs as a non-root user and writes debug logs here.
mkdir -p logs && chmod 777 logs

echo "Building and starting containers (the first run takes a few minutes)..."
docker compose -f docker-compose.app.yml up -d --build

echo "Waiting for $url ..."
for _ in $(seq 60); do
  if curl -fsS "$url/api/v1/health" >/dev/null 2>&1; then
    echo "Running at $url  (stop with ./stop-app.sh)"
    if command -v open >/dev/null; then open "$url"; elif command -v xdg-open >/dev/null; then xdg-open "$url"; fi
    exit 0
  fi
  sleep 2
done
echo "The app did not respond. Check: docker compose -f docker-compose.app.yml logs"
exit 1
