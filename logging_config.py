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

    if use_console:
        # ConsoleRenderer는 exc_info를 자체적으로 예쁘게 렌더링한다.
        # 앞에 format_exc_info를 두면 그 처리를 가로채므로 넣지 않는다.
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        # JSONRenderer는 exc_info를 해석하지 못해 `"exc_info": true` 한 줄만 남기고
        # 트레이스백을 통째로 버린다. 그 결과 log.exception()이 console(로컬)에서는
        # 멀쩡하고 json(프로덕션 기본값)에서만 스택을 잃는다 — 정작 장애가 난 곳에서
        # 추적이 안 되는 최악의 형태라, 렌더링 전에 문자열로 펼쳐 둔다.
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())

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
