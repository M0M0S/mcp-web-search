"""Core module - configuration, logging, dependencies."""

from .config import Settings
from .dependencies import auth_provider, get_redis
from .logging import setup_logging

__all__ = ["Settings", "setup_logging", "get_redis", "auth_provider"]
