"""Validación de los payloads de entrada, por acción."""

from __future__ import annotations

from typing import Any

from errors import ValidationError

VALID_ACTIONS = {"pending", "processed"}


def validate_action(payload: dict[str, Any]) -> str:
    """Valida y retorna el campo ``action``.

    Raises:
        ValidationError: Si falta, no es texto, o no es una acción soportada.
    """
    action = payload.get("action")
    if not action or not isinstance(action, str):
        raise ValidationError("El campo 'action' es obligatorio y debe ser una cadena de texto.")
    if action not in VALID_ACTIONS:
        raise ValidationError(
            f"Acción no soportada: '{action}'. Acciones válidas: {', '.join(sorted(VALID_ACTIONS))}."
        )
    return action


def validate_pending_payload(payload: dict[str, Any], expected_prefix: str) -> str:
    """Valida el campo ``prefix`` de la acción 'pending'.

    Por seguridad, el prefijo solicitado debe coincidir exactamente con el
    ``SOURCE_PREFIX`` configurado en la Lambda: el cliente no puede pedir un
    prefijo arbitrario del bucket.

    Raises:
        ValidationError: Si falta, no es texto, o no coincide con el prefijo configurado.
    """
    prefix = payload.get("prefix")
    if not prefix or not isinstance(prefix, str):
        raise ValidationError("El campo 'prefix' es obligatorio y debe ser una cadena de texto.")

    normalized = prefix if prefix.endswith("/") else f"{prefix}/"
    if normalized != expected_prefix:
        raise ValidationError(
            f"'prefix' no coincide con el prefijo configurado en el servicio ('{expected_prefix}')."
        )
    return normalized


def validate_processed_payload(payload: dict[str, Any]) -> tuple[str, str, str | None]:
    """Valida los campos ``requestId``, ``id`` y el opcional ``processedPrefix``
    de la acción 'processed'.

    ``processedPrefix`` permite al cliente elegir, en cada llamada, el
    prefijo base del bucket donde se mueve el archivo (en vez de usar
    siempre el ``PROCESSED_PREFIX`` fijo configurado en la Lambda). Si no se
    envía, se usa ese valor por defecto.

    Returns:
        Tupla ``(request_id, id_token, processed_prefix)``. ``processed_prefix``
        es ``None`` si el cliente no envió ``processedPrefix``.

    Raises:
        ValidationError: Si falta ``requestId`` o ``id``, o si ``processedPrefix``
            se envía pero no es una cadena de texto no vacía.
    """
    request_id = payload.get("requestId")
    if not request_id or not isinstance(request_id, str):
        raise ValidationError("El campo 'requestId' es obligatorio y debe ser una cadena de texto.")

    id_token = payload.get("id")
    if not id_token or not isinstance(id_token, str):
        raise ValidationError("El campo 'id' es obligatorio y debe ser una cadena de texto.")

    processed_prefix = payload.get("processedPrefix")
    if processed_prefix is not None:
        if not isinstance(processed_prefix, str) or not processed_prefix.strip():
            raise ValidationError(
                "El campo 'processedPrefix' debe ser una cadena de texto no vacía."
            )
        processed_prefix = processed_prefix.strip()
        if not processed_prefix.endswith("/"):
            processed_prefix += "/"

    return request_id, id_token, processed_prefix
