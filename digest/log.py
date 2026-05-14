"""Structlog setup shared by the runner and the FastAPI server.

JSON output, ISO timestamps, log level. Idempotent — safe to call from multiple
entry points (runner CLI, runner_fargate container, FastAPI lifespan).
"""

from __future__ import annotations

import logging
import sys

import structlog

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Set up stdlib + structlog with a JSON renderer. Call once at startup."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    _CONFIGURED = True
