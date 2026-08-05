"""
Minimal structured logging. Not a full observability stack (no Sentry, no
external log shipping) — just consistent, greppable log lines so errors and
key events (uploads, rate-limit hits) show up clearly in Vercel's function
logs or any `docker logs` / journalctl output, without adding a paid
third-party dependency to a demo project.

For real production use, swap get_logger()'s handler for whatever your
hosting platform's log pipeline expects (e.g. send to Sentry, Datadog, etc.)
— the call sites in main.py won't need to change.

GOTCHA when adding new log calls: Python's logging module reserves certain
attribute names on LogRecord (filename, module, funcName, lineno, name,
msg, args, process, thread, ...) — passing extra={"filename": ...} raises
a KeyError at call time, not at import time, so it's easy to miss until a
request actually hits that code path (which is exactly how this was found
— caught by the test suite, not by manual testing). Prefix ambiguous keys
(e.g. "upload_filename" instead of "filename") to stay safe.
"""

import json
import logging
import sys
import time


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Any extra fields passed via logger.info(..., extra={...}) get merged in.
        for key, value in record.__dict__.items():
            if key not in payload and key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelno", "lineno", "module", "msecs", "msg", "name",
                "pathname", "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName", "levelname", "taskName",
            ):
                payload[key] = value
        return json.dumps(payload, default=str)


def get_logger(name: str = "sheetvaidya") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:  # avoid duplicate handlers on module reload
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
