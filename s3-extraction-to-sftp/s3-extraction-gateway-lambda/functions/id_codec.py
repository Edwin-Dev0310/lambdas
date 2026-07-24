"""Codificación/decodificación del campo ``id`` expuesto por la acción 'pending'.

Diseño: ``id`` es la propia ``key`` de S3 codificada en Base64 URL-safe (sin
padding). No requiere estado adicional (DynamoDB u otro almacenamiento): la
acción 'processed' puede resolver la ``key`` original únicamente a partir del
``id`` recibido, sin depender de ``requestId`` ni de ninguna tabla externa.

No hay ningún problema de seguridad en que ``id`` sea reversible a partir de
la ``key``: la misma ``key`` ya se devuelve en texto plano en el campo
``key`` de la respuesta de 'pending'. ``id`` es simplemente una
representación estable y URL-safe de esa misma información, apta para viajar
como valor de un campo JSON.
"""

from __future__ import annotations

import base64
import binascii


def encode_key(key: str) -> str:
    """Codifica una ``key`` de S3 como ``id`` opaco (Base64 URL-safe, sin '=')."""
    raw = base64.urlsafe_b64encode(key.encode("utf-8"))
    return raw.decode("ascii").rstrip("=")


def decode_key(id_token: str) -> str:
    """Decodifica un ``id`` de vuelta a la ``key`` de S3 original.

    Raises:
        ValueError: Si ``id_token`` no es una codificación Base64 URL-safe válida.
    """
    padding = "=" * (-len(id_token) % 4)
    try:
        raw = base64.urlsafe_b64decode(id_token + padding)
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"El campo 'id' no es válido: '{id_token}'.") from exc
