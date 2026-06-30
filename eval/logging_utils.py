from __future__ import annotations

import logging
import os


DEFAULT_LOG_FORMAT = (
    "ts=%(asctime)s level=%(levelname)s logger=%(name)s "
    "message=%(message)s"
)


def configure_logging(level: str | None = None) -> None:
    resolved_level = (level or os.environ.get("LLM_MTD_EVAL_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, resolved_level, logging.INFO),
        format=DEFAULT_LOG_FORMAT,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
