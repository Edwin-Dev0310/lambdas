"""Construcción de respuestas HTTP para integración proxy de API Gateway."""

from __future__ import annotations

import json
from typing import Any


def build_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Construye una respuesta compatible con Lambda Proxy Integration.

    Args:
        status_code: Código HTTP a devolver (200, 400, 404, 500, ...).
        body: Diccionario que se serializa como JSON en el cuerpo de la respuesta.

    Returns:
        Diccionario con ``statusCode``, ``headers`` y ``body`` tal como lo
        espera API Gateway.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
