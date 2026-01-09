# logging_setup.py
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_configured = False


def configure_logging(log_file: Optional[str] = None, log_level: Optional[str] = None) -> None:
    global _configured
    if _configured:
        return
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()

    handlers = [logging.StreamHandler()]

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    _configured = True
