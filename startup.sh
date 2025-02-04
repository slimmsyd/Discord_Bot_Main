#!/bin/bash
# Make sure the script is executable
chmod +x startup.sh

# Enable logging
export PYTHONUNBUFFERED=1

# Start Gunicorn with the correct worker class and logging
exec gunicorn \
    --bind 0.0.0.0:8000 \
    --worker-class aiohttp.GunicornWebWorker \
    --workers 1 \
    --timeout 120 \
    --log-level debug \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --enable-stdio-inheritance \
    app:app 