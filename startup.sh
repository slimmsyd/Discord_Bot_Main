#!/bin/bash
# Make sure the script is executable
chmod +x startup.sh

# Start Gunicorn with the correct worker class
exec gunicorn --bind 0.0.0.0:8000 \
    --worker-class aiohttp.GunicornWebWorker \
    --workers 1 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    app:app 