FROM python:3.9-slim

# Install required system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Azure-specific configurations
ENV WEBSITES_PORT=8000
EXPOSE 8000

# Use production-grade server (install gunicorn in requirements.txt)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--worker-class", "aiohttp.GunicornWebWorker", "app:app"] 