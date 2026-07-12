"""Логирование: файл на день, ротация, очистка старых."""

import logging
import logging.handlers
import sys

from . import config


def setup() -> logging.Logger:
    config.init_dirs()
    log_file = config.LOGS_DIR / "bot.log"

    handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=config.LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=False,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)-10s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # очистим старые хендлеры, если повторно вызвали setup
    root.handlers = [handler, stream]

    logging.getLogger("aiogram.event").setLevel(logging.INFO)
    logging.getLogger("aiogram.dispatcher").setLevel(logging.INFO)

    return logging.getLogger("bridge")
