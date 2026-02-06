import logging.config
import os
from app.core.env import load_app_env
load_app_env()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s]: %(name)s - %(message)s"
        },
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "json_ensure_ascii": False,
            "json_indent": 2
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": LOG_LEVEL,
            "formatter": "json",
        },
        "file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": LOG_LEVEL,
            "formatter": "standard",
            "filename": "logs/app.log",
            "when": "midnight",
            "backupCount": 7,
        },
    },

    "root": {
        "level": "INFO",
        "handlers": ["console", "file"],
    },
}


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    logging.config.dictConfig(LOGGING_CONFIG)
