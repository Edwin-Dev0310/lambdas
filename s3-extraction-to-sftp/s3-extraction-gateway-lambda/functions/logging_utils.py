"""Logging estructurado (JSON) para CloudWatch Logs.

CloudWatch Logs captura automáticamente todo lo escrito a stdout/stderr por
la Lambda; aquí solo se define el formato (JSON) y un helper para registrar
eventos de negocio de forma consistente (acción, estado, detalle).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any


class JsonFormatter(logging.Formatter):
    """Formatea cada registro de log como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str = "s3_extraction_gateway") -> logging.Logger:
    """Crea (o reutiliza) un logger con formato JSON hacia stdout.

    En Lambda, todo lo escrito a stdout se envía automáticamente al Log
    Group de CloudWatch asociado a la función.
    """
    logger = logging.getLogger(name)
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger


def log_event(logger: logging.Logger, *, action: str, status: str, **fields: Any) -> None:
    """Registra un evento de negocio con un formato consistente.

    Args:
        logger: Logger ya configurado (ver :func:`get_logger`).
        action: Acción del servicio ("pending", "processed").
        status: Estado del evento (p. ej. "RECEIVED", "OK", "MOVED",
            "VALIDATION_ERROR", "NOT_FOUND", "ERROR").
        **fields: Campos adicionales a incluir en el log (request_id, count, etc.).
    """
    message = f"action='{action}' status='{status}'"
    extra = {"extra_fields": {"action": action, "status": status, **fields}}

    if status.upper() in {"ERROR", "FAILED"}:
        logger.error(message, extra=extra)
    elif status.upper() in {"VALIDATION_ERROR", "NOT_FOUND"}:
        logger.warning(message, extra=extra)
    else:
        logger.info(message, extra=extra)
