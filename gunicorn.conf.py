import multiprocessing

# Gunicorn config
bind = "0.0.0.0:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "aiohttp.worker.GunicornWebWorker"
timeout = 120 