"""Logs that are safe to keep and to share: they say that a request happened, never what it held.

Never in a log: a data value, a file's content, a question typed to Lens, an AI reply, a search term. Requests carry those in their body or their query string, so the access log keeps the method, the path (a run id),
the status and nothing after the "?"; an unhandled error is logged by its type and where it happened, without its message (which may quote a value) or its traceback. Applied by `python -m app` and by `configure()`."""
from __future__ import annotations

import logging

SAFE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class StripQuery(logging.Filter):
    """uvicorn's access line is `client - "METHOD /path?query HTTP/1.1" status`: the query string is cut off."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


class NoMessagesNoTracebacks(logging.Filter):
    """An error record keeps its logger, its level and the exception's type; its message and traceback (which can quote a cell, a column's values or a question) are dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            kind = record.exc_info[0].__name__ if record.exc_info[0] else "error"
            record.msg, record.args = f"unhandled {kind} (details withheld: they can quote what the user sent)", ()
            record.exc_info, record.exc_text = None, None
        return True


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"strip_query": {"()": "app.logsafe.StripQuery"}, "no_details": {"()": "app.logsafe.NoMessagesNoTracebacks"}},
    "formatters": {"safe": {"format": SAFE_FORMAT}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "safe", "stream": "ext://sys.stdout", "filters": ["strip_query", "no_details"]}},
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "app": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
}


def configure() -> None:
    import logging.config
    logging.config.dictConfig(LOG_CONFIG)
