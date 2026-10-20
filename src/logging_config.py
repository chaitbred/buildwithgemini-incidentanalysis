"""Central logging setup.

Logger namespace (all under ``incident``) decides which file a record lands in:

    incident.llm.*       -> logs/llm.log       (provider init, calls, retries, latency, unavailability)
    incident.pipeline.*  -> logs/pipeline.log  (batch/classification/taxonomy: progress + timings)
    incident.storage.*   -> logs/storage.log   (storage backends)
    incident.web.*       -> logs/web.log       (HTTP requests, uploads, endpoints)
    incident.tools.*     -> logs/tools.log     (postmortem, runbooks, video, external status)
    incident.*  (all)    -> logs/app.log       (everything, combined timeline)
    WARNING+ (all)       -> logs/error.log     (quick triage of technical problems)

Env vars: LOG_LEVEL (default INFO), LOG_DIR (default ./logs), LOG_FORMAT
(``text`` default | ``json``), LOG_CONSOLE (default true).

Usage::

    from src.logging_config import get_logger, log_timing
    logger = get_logger("llm.client")          # -> "incident.llm.client"
    with log_timing(logger, "classify batch", items=n):
        ...
"""
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from functools import wraps
from logging.handlers import RotatingFileHandler

ROOT = "incident"
_CONFIGURED = False

_TEXT_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# file name -> logger sub-namespace routed to it (None = everything)
_CHANNELS = {
    "app.log": None,
    "llm.log": "llm",
    "pipeline.log": "pipeline",
    "storage.log": "storage",
    "web.log": "web",
    "tools.log": "tools",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


class _NamespaceFilter(logging.Filter):
    def __init__(self, prefix: str):
        super().__init__()
        self._prefix = f"{ROOT}.{prefix}"

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == self._prefix or record.name.startswith(self._prefix + ".")


def setup_logging() -> None:
    """Idempotently configure the ``incident`` logger tree."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_dir = os.path.abspath(os.getenv("LOG_DIR", "logs"))
    formatter: logging.Formatter = (
        _JsonFormatter() if os.getenv("LOG_FORMAT", "text").lower() == "json"
        else logging.Formatter(_TEXT_FMT)
    )

    root = logging.getLogger(ROOT)
    root.setLevel(level)
    root.propagate = False

    if os.getenv("LOG_CONSOLE", "true").lower() != "false":
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        for fname, ns in _CHANNELS.items():
            handler = RotatingFileHandler(
                os.path.join(log_dir, fname), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(formatter)
            if ns:
                handler.addFilter(_NamespaceFilter(ns))
            root.addHandler(handler)

        errors = RotatingFileHandler(
            os.path.join(log_dir, "error.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        errors.setLevel(logging.WARNING)
        errors.setFormatter(formatter)
        root.addHandler(errors)
    except OSError as e:  # never let logging break the app
        root.warning("File logging disabled (cannot write to %s): %s", log_dir, e)

    root.info("Logging initialised level=%s dir=%s", logging.getLevelName(level), log_dir)


def get_logger(name: str) -> logging.Logger:
    """Return ``incident.<name>``, ensuring logging is configured."""
    setup_logging()
    return logging.getLogger(f"{ROOT}.{name}")


@contextmanager
def log_timing(logger: logging.Logger, operation: str, level: int = logging.INFO, **context):
    """Log start/end (with duration) of an operation; logs exception + re-raises on failure."""
    ctx = " ".join(f"{k}={v}" for k, v in context.items())
    logger.log(level, "START %s %s", operation, ctx)
    start = time.perf_counter()
    try:
        yield
    except Exception:
        logger.exception("FAILED %s after %.2fs %s", operation, time.perf_counter() - start, ctx)
        raise
    logger.log(level, "DONE %s in %.2fs %s", operation, time.perf_counter() - start, ctx)


def timed(logger: logging.Logger, operation: str = "", level: int = logging.DEBUG):
    """Decorator form of ``log_timing`` for sync functions."""
    def deco(fn):
        name = operation or fn.__qualname__

        @wraps(fn)
        def wrapper(*a, **kw):
            with log_timing(logger, name, level=level):
                return fn(*a, **kw)
        return wrapper
    return deco


def log_progress(logger: logging.Logger, operation: str, done: int, total: int, every: int = 10,
                 started: float | None = None) -> None:
    """Log progress every ``every`` items and at completion (cheap for heavy loops)."""
    if total and (done % every == 0 or done == total):
        elapsed = f" elapsed={time.perf_counter() - started:.1f}s" if started else ""
        logger.info("%s progress %d/%d (%.0f%%)%s", operation, done, total, 100 * done / total, elapsed)
