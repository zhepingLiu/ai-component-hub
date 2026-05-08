from __future__ import annotations

import atexit
import contextvars
import gzip
import json
import logging
import os
import queue
import shutil
import sys
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from typing import Any


_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("gateway_log_context", default={})
_RUNTIME_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_MANAGED_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore")
_LOG_LISTENER: QueueListener | None = None
_CONFIGURED = False


def set_log_context(**kwargs: Any):
    current = dict(_LOG_CONTEXT.get({}))
    current.update({k: v for k, v in kwargs.items() if v is not None})
    return _LOG_CONTEXT.set(current)


def update_log_context(**kwargs: Any) -> None:
    current = dict(_LOG_CONTEXT.get({}))
    current.update({k: v for k, v in kwargs.items() if v is not None})
    _LOG_CONTEXT.set(current)


def reset_log_context(token) -> None:
    _LOG_CONTEXT.reset(token)


def get_log_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get({}))


def _normalize_level(level: str) -> str:
    normalized = str(level or "").strip().upper()
    if normalized not in logging._nameToLevel:
        raise ValueError(f"unsupported log level: {level}")
    return normalized


def get_runtime_log_level() -> str:
    root_level = logging.getLogger().getEffectiveLevel()
    if root_level > 0:
        return logging.getLevelName(root_level)
    return _RUNTIME_LOG_LEVEL


def set_runtime_log_level(level: str) -> str:
    global _RUNTIME_LOG_LEVEL
    normalized = _normalize_level(level)
    _RUNTIME_LOG_LEVEL = normalized

    root = logging.getLogger()
    root.setLevel(normalized)
    for logger_name, logger_obj in logging.root.manager.loggerDict.items():
        if isinstance(logger_obj, logging.Logger) and logger_name.startswith("gateway"):
            logger_obj.setLevel(normalized)
    for name in _MANAGED_LOGGER_NAMES:
        logging.getLogger(name).setLevel(normalized)
    return get_runtime_log_level()


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") + f":{value.microsecond // 1000:03d}"


def _format_prefix_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _message_text(payload: dict[str, Any], record: logging.LogRecord) -> str:
    if "message" in payload and payload["message"] is not None:
        return str(payload["message"])
    if "event" in payload and payload["event"] is not None:
        return str(payload["event"])
    if isinstance(record.msg, dict):
        return json.dumps(payload, ensure_ascii=False, default=str)
    return record.getMessage()


def _normalize_prefix_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("{'") and text.endswith("'}"):
        text = text[2:-2]
    elif text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return " ".join(text.split())


def outbound_extra(**audit_fields: Any) -> dict[str, Any]:
    return {"log_kind": "out", "audit_fields": audit_fields}


class SizeAndDateRotatingFileHandler(RotatingFileHandler):
    def __init__(
        self,
        *,
        log_dir: str,
        archive_dir: str,
        system_code: str,
        module_name: str,
        log_kind: str,
        max_bytes: int,
        backup_count: int,
    ):
        self.log_dir = log_dir
        self.archive_dir = archive_dir
        self.system_code = system_code
        self.module_name = module_name
        self.log_kind = log_kind
        self.current_date = self._date_str()
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)
        super().__init__(
            self._build_filename(self.current_date),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self.namer = self._namer
        self.rotator = self._rotator

    def _date_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _build_filename(self, date_str: str) -> str:
        return os.path.join(
            self.log_dir,
            f"{self.system_code}.{self.module_name}.{self.log_kind}.log.{date_str}",
        )

    def _namer(self, default_name: str) -> str:
        filename = os.path.basename(default_name)
        return os.path.join(self.archive_dir, f"{filename}.gz")

    def _rotator(self, source: str, dest: str) -> None:
        with open(source, "rb") as src, gzip.open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.remove(source)

    def _archive_file(self, file_path: str) -> None:
        if not file_path or not os.path.exists(file_path) or os.path.isdir(file_path):
            return
        archived_path = self._namer(file_path)
        if os.path.exists(archived_path):
            os.remove(archived_path)
        self._rotator(file_path, archived_path)

    def _switch_to_current_date(self) -> None:
        date_str = self._date_str()
        if date_str == self.current_date:
            return
        previous_filename = self.baseFilename
        self.current_date = date_str
        if self.stream:
            self.stream.close()
            self.stream = None
        self._archive_file(previous_filename)
        self.baseFilename = os.path.abspath(self._build_filename(date_str))

    def emit(self, record: logging.LogRecord) -> None:
        self._switch_to_current_date()
        super().emit(record)


class LogKindFilter(logging.Filter):
    def __init__(self, *accepted: str):
        super().__init__()
        self.accepted = set(accepted)

    def filter(self, record: logging.LogRecord) -> bool:
        return _resolve_log_kind(record) in self.accepted


class ContextSnapshotFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.log_context_snapshot = get_log_context()
        return True


class ContextQueueHandler(QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return logging.makeLogRecord(record.__dict__.copy())


def _resolve_log_kind(record: logging.LogRecord) -> str:
    explicit = getattr(record, "log_kind", None)
    if explicit:
        return str(explicit)
    if record.levelno >= logging.ERROR:
        return "error"
    return "monitor"


class MixedFormatter(logging.Formatter):
    def __init__(self, service_name: str, system_code: str):
        super().__init__()
        self.service_name = service_name
        self.system_code = system_code

    def format(self, record: logging.LogRecord) -> str:
        payload = record.msg if isinstance(record.msg, dict) else {"message": record.getMessage()}
        audit_fields = dict(getattr(record, "log_context_snapshot", {}) or {})
        audit_fields.update(getattr(record, "audit_fields", {}) or {})
        created_at = datetime.fromtimestamp(record.created)
        message_text = _message_text(payload, record)
        prefix_message = _normalize_prefix_text(message_text)
        trace_id = payload.get("traceId") or payload.get("trace_id") or audit_fields.get("traceId", "")
        seq_no = payload.get("seqNo") or payload.get("request_id") or audit_fields.get("seqNo", "")
        base = {
            "ts": _format_timestamp(created_at),
            "level": record.levelname,
            "traceId": str(trace_id or ""),
            "threadName": self.service_name,
            "location": f"{record.filename}:{record.lineno}",
            "message": message_text,
            "service": self.service_name,
            "logger": record.name,
            "sysName": self.system_code,
            "seqNo": str(seq_no or ""),
            "tradeType": "ss",
            "serverReceiveTime": str(payload.get("serverReceiveTime") or audit_fields.get("serverReceiveTime", "")),
            "serverReturnTime": str(payload.get("serverReturnTime") or audit_fields.get("serverReturnTime", "")),
            "usedTime": str(payload.get("usedTime") or audit_fields.get("usedTime", "")),
        }
        base.update(payload)
        base["tradeType"] = "ss"

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)

        prefix = (
            f"{_format_prefix_timestamp(created_at)} "
            f"{record.levelname} "
            f"[{str(trace_id or '')}] "
            f"[{self.service_name}] "
            f"[{record.filename}:{record.lineno}] - "
            f"{prefix_message}"
        )
        return f"{prefix} {json.dumps(base, ensure_ascii=False, default=str)}"


def setup_logging(
    service_name: str,
    log_dir: str,
    level: str,
    retention_days: int,
    *,
    system_code: str,
    max_bytes: int,
) -> None:
    global _CONFIGURED, _LOG_LISTENER
    if _CONFIGURED:
        return

    root = logging.getLogger()
    normalized_level = _normalize_level(level)
    root.setLevel(normalized_level)
    root.handlers.clear()
    archive_dir = os.path.join(log_dir, "hislog")
    formatter = MixedFormatter(service_name, system_code)

    monitor_handler = SizeAndDateRotatingFileHandler(
        log_dir=log_dir,
        archive_dir=archive_dir,
        system_code=system_code,
        module_name=service_name,
        log_kind="monitor",
        max_bytes=max_bytes,
        backup_count=retention_days,
    )
    monitor_handler.setFormatter(formatter)
    monitor_handler.addFilter(LogKindFilter("monitor"))

    out_handler = SizeAndDateRotatingFileHandler(
        log_dir=log_dir,
        archive_dir=archive_dir,
        system_code=system_code,
        module_name=service_name,
        log_kind="out",
        max_bytes=max_bytes,
        backup_count=retention_days,
    )
    out_handler.setFormatter(formatter)

    error_handler = SizeAndDateRotatingFileHandler(
        log_dir=log_dir,
        archive_dir=archive_dir,
        system_code=system_code,
        module_name=service_name,
        log_kind="error",
        max_bytes=max_bytes,
        backup_count=retention_days,
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = ContextQueueHandler(log_queue)
    queue_handler.addFilter(ContextSnapshotFilter())
    root.addHandler(queue_handler)

    _LOG_LISTENER = QueueListener(
        log_queue,
        monitor_handler,
        out_handler,
        error_handler,
        stream_handler,
        respect_handler_level=True,
    )
    _LOG_LISTENER.start()

    for name in _MANAGED_LOGGER_NAMES:
        logging.getLogger(name).setLevel(normalized_level)

    set_runtime_log_level(normalized_level)
    _CONFIGURED = True


@atexit.register
def _shutdown_log_listener() -> None:
    global _LOG_LISTENER
    if _LOG_LISTENER is not None:
        _LOG_LISTENER.stop()
        _LOG_LISTENER = None
