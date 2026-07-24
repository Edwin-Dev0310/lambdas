"""Excepciones de dominio del servicio.

Se mapean explícitamente a códigos HTTP en ``handler.py``:
    ValidationError -> 400
    NotFoundError    -> 404
    (cualquier otra) -> 500
"""

from __future__ import annotations


class ValidationError(Exception):
    """La petición del cliente es inválida o le faltan parámetros (HTTP 400)."""


class NotFoundError(Exception):
    """El recurso solicitado (archivo) no existe en el bucket (HTTP 404)."""


class ConfigError(Exception):
    """Falta o es inválida una variable de entorno requerida por el servicio."""
