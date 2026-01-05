import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = record.msg if isinstance(record.msg, dict) else {"message": record.getMessage()}
        base = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
        }
        base.update(payload)

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(base, ensure_ascii=True)


def setup_logging(service_name: str, log_dir: str, level: str, retention_days: int) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(level.upper())
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"{service_name}.log")
    formatter = JsonFormatter(service_name)

    file_handler = TimedRotatingFileHandler(
        log_path, when="D", interval=1, backupCount=retention_days, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    _CONFIGURED = True


_CONFIGURED = False
