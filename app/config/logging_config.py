import logging
import logging.config
import os

from app.core.env import load_app_env

load_app_env()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = os.getenv("LOG_DIR", "logs")
DEBUG_LOG_DIR = os.getenv("DEBUG_LOG_DIR", f"{LOG_DIR}/debug_logs")
INFO_LOG_DIR = os.getenv("INFO_LOG_DIR", f"{LOG_DIR}/info_logs")
ERROR_LOG_DIR = os.getenv("ERROR_LOG_DIR", f"{LOG_DIR}/error_logs")


class DebugOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == logging.DEBUG


class InfoWarningOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno in (logging.INFO, logging.WARNING)


class ErrorAndAboveFilter(logging.Filter):
    """ERROR, CRITICAL, and logger.exception() (logged at ERROR with exc_info)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s [%(levelname)s]: %(name)s - %(message)s"},
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "json_ensure_ascii": False,
            "json_indent": 2,
        },
    },
    "filters": {
        "debug_only": {
            "()": "app.config.logging_config.DebugOnlyFilter",
        },
        "info_warning_only": {
            "()": "app.config.logging_config.InfoWarningOnlyFilter",
        },
        "error_and_above": {
            "()": "app.config.logging_config.ErrorAndAboveFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": LOG_LEVEL,
            "formatter": "json",
        },
        "debug_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": "DEBUG",
            "formatter": "json",
            "filename": f"{DEBUG_LOG_DIR}/debug.log",
            "when": "midnight",
            "backupCount": 3,
            "filters": ["debug_only"],
        },
        "info_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": "INFO",
            "formatter": "json",
            "filename": f"{INFO_LOG_DIR}/app.log",
            "when": "midnight",
            "backupCount": 7,
            "filters": ["info_warning_only"],
        },
        "error_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": "ERROR",
            "formatter": "json",
            "filename": f"{ERROR_LOG_DIR}/error.log",
            "when": "midnight",
            "backupCount": 14,
            "filters": ["error_and_above"],
        },
    },
    "root": {
        "level": LOG_LEVEL,
        "handlers": ["console", "debug_file", "info_file", "error_file"],
    },
}


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(DEBUG_LOG_DIR, exist_ok=True)
    os.makedirs(INFO_LOG_DIR, exist_ok=True)
    os.makedirs(ERROR_LOG_DIR, exist_ok=True)
    logging.config.dictConfig(LOGGING_CONFIG)
