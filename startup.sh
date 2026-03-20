#!/bin/bash
# Startup script for Azure App Service
# This script starts the FastAPI application using Gunicorn with Uvicorn workers

cd /home/site/wwwroot

# Ensure the app root is on Python's module path
export PYTHONPATH="/home/site/wwwroot:$PYTHONPATH"

echo "=== Startup: $(date -u) ==="
echo "Python: $(python --version 2>&1)"

# Start the application (1 worker to avoid duplicate agent background threads)
# --timeout 120 for slow MI token acquisition on first request
gunicorn src.webapp.main:app \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
