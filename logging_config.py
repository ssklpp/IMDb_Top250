import logging
import os
import sys
from contextvars import ContextVar

import structlog

session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def _inject_context(_, __, event_dict):
    sid = session_id_var.get()
    rid = request_id_var.get()
    if sid:
        event_dict.setdefault("session_id", sid)
    if rid:
        event_dict.setdefault("request_id", rid)
    return event_dict


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_console = os.environ.get("LOG_FORMAT", "json").lower() == "console"

    processors = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    processors.append(
        structlog.dev.ConsoleRenderer()
        if use_console
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stderr,
        force=True,
    )

    _configured = True


def get_logger(name: str = "app"):
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)
