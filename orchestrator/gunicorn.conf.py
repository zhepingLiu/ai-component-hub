import os


workers = int(os.getenv("WEB_CONCURRENCY", "4"))
bind = "0.0.0.0:7010"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

