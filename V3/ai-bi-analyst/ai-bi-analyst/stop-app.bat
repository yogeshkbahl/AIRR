@echo off
rem Stops AI BI Analyst. Uploaded datasets and logs are kept.
cd /d "%~dp0"
docker compose -f docker-compose.app.yml down
echo AI BI Analyst stopped.
timeout /t 3 >nul
