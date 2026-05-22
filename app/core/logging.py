"""Logging configuration — guaranteed to work in Docker + FastMCP (structlog 25.1+)."""

import logging

import structlog

from app.core.config import Settings


def setup_logging(settings: Settings) -> None:
    """Simplest and most reliable configuration for dev and Docker."""
    # Configure Python logging to handle DEBUG level
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "web-search"):
    """Get logger (call only AFTER setup_logging)."""
    return structlog.get_logger(name)
