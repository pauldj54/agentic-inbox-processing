#!/bin/bash
# Startup script for Azure App Service
# This script starts the FastAPI application using Gunicorn with Uvicorn workers

cd /home/site/wwwroot

# Install dependencies if needed
pip install -r requirements.txt

# Start the application
gunicorn src.webapp.main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
