import logging.config
import os

from app.core.env import load_app_env

load_app_env()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

LOGGING_CONFIG = {
    "version": 1,
    # mandatory field i.e version 1 is required
    "disable_existing_loggers": False,
    # tells to not disable other loggers if any from the imported modules
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
        # the keys "console" and "file" are user defined labels. they should match the values in the root.handlers list
        "console": {
            "class": "logging.StreamHandler",
            #class tells the logger which class to use to handle the log. The rest of the keys are the attributes of the class.
            "level": LOG_LEVEL,
            "formatter": "json",
        },
        "file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": LOG_LEVEL,
            "formatter": "json",
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
