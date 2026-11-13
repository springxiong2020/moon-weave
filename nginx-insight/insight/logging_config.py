"""Shared CLI/Gunicorn logging; redact credentials even in exception traces."""

import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler


class SafeFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record):
        text = super().format(record)
        for name in (
            "BAIDU_MAP_AK",
            "DASHBOARD_PASSWORD",
            "SESSION_SECRET",
            "MYSQL_PASSWORD",
            "MYSQL_ROOT_PASSWORD",
        ):
            secret = os.getenv(name)
            if secret:
                text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"(\b[a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]*@",
            r"\1[REDACTED]@",
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r'([?&]ak=)[^&\s\'"<>]+', r"\1[REDACTED]", text, flags=re.IGNORECASE
        )


def configure_logging(level=None):
    logger = logging.getLogger("insight")
    logger.setLevel(level or os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    # Reusing an app factory must not duplicate handlers.
    if logger.handlers:
        return
    formatter = SafeFormatter(
        "%(asctime)sZ %(levelname)s %(name)s pid=%(process)d %(message)s",
        "%Y-%m-%dT%H:%M:%S",
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    filename = os.getenv("LOG_FILE")
    if filename:
        handler = RotatingFileHandler(
            filename, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
