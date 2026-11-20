"""Configuración de logging con rotación automática a archivo y consola.

Todo lo que se registra va tanto a ``log_file`` (con rotación) como a
stdout, para que sea visible tanto en ``logs/service.log`` como en la
salida capturada por el CRON/Task Scheduler (por ejemplo, redirigida con
``>> cron.log 2>&1``).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB por archivo de log
_BACKUP_COUNT = 5  # 5 archivos históricos ademas del activo


def build_logger(name: str, log_file: Path, level: str = "INFO") -> logging.Logger:
    """Crea (o reutiliza) un logger con rotación automática de archivos.

    Args:
        name: Nombre del logger (namespace de ``logging``).
        log_file: Ruta del archivo de log; se crean las carpetas necesarias.
        level: Nivel mínimo de logging (DEBUG, INFO, WARNING, ERROR).

    Returns:
        Logger configurado con un ``RotatingFileHandler`` y un
        ``StreamHandler`` (consola), ambos con el mismo formato.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level.upper())

    if not logger.handlers:
        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

        file_handler = RotatingFileHandler(
            log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        logger.propagate = False

    return logger
