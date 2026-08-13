"""Configuration-first logging setup."""

import logging.config

from app.config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Apply already validated logging configuration."""

    logging.config.dictConfig(config.as_dict())
