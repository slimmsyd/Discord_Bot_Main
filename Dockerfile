FROM python:3.11-slim

# Install required system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libasound2-dev \
    python3-all-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Health server inside app.py binds 8000 (optional; not required for a gateway bot)
EXPOSE 8000

# Run the bot directly. NOTE: do NOT use gunicorn/startup.sh here — that path
# serves the web app but never calls bot.start(), so the bot never connects.
CMD ["python", "app.py"]
