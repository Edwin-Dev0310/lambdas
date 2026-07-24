"""Punto de entrada de la Lambda: enruta acciones y traduce errores a HTTP.

Soporta dos formas de evento:
    1. Evento proxy de API Gateway (producción): el JSON de entrada viaja en
       ``event['body']`` como cadena de texto.
    2. Evento de prueba invocado directamente desde la consola de Lambda: el
       JSON de entrada es el propio ``event`` (contiene la clave ``action``).

La configuración (variables de entorno) y el cliente S3 se inicializan una
sola vez por contenedor (fuera del handler) para reutilizarse entre
invocaciones "warm", siguiendo las buenas prácticas de rendimiento en Lambda.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from config import load_settings
from errors import ConfigError, NotFoundError, ValidationError
from id_codec import decode_key
from logging_utils import get_logger, log_event
from responses import build_response
from s3_service import S3Service
from validators import validate_action, validate_pending_payload, validate_processed_payload

logger = get_logger()

# --------------------------------------------------------------------------- #
# Inicialización en frío (cold start): se ejecuta una sola vez por contenedor.
# --------------------------------------------------------------------------- #
_config_error: str | None = None
try:
    _settings = load_settings()
except ConfigError as exc:
    _settings = None
    _config_error = str(exc)
    logger.error("Error de configuración en el arranque de la Lambda: %s", exc)

_s3_service = S3Service(_settings.bucket_name, logger) if _settings else None


def _extract_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Obtiene el JSON de entrada, ya sea desde un evento proxy de API Gateway
    o desde un evento de prueba invocado directamente en la consola de Lambda.

    Raises:
        ValidationError: Si no hay un cuerpo JSON válido en el evento.
    """
    body = event.get("body")
    if body is not None:
        try:
            return json.loads(body) if isinstance(body, str) else body
        except json.JSONDecodeError as exc:
            raise ValidationError("El cuerpo de la petición no es un JSON válido.") from exc

    if "action" in event:
        return event

    raise ValidationError("No se encontró un cuerpo JSON válido en la petición.")


def _handle_pending(payload: dict[str, Any]) -> dict[str, Any]:
    """Acción 'pending': todos los archivos actualmente bajo SOURCE_PREFIX.

    Cada archivo incluye su URL prefirmada de descarga (``downloadUrl``); no
    se necesita una llamada adicional para obtenerla.
    """
    validate_pending_payload(payload, _settings.source_prefix)

    request_id = str(uuid.uuid4())
    files = _s3_service.list_pending_files(_settings.source_prefix, _settings.url_expiration_seconds)

    log_event(logger, action="pending", status="OK", request_id=request_id, count=len(files))
    return build_response(200, {"requestId": request_id, "success": True, "files": files})


def _handle_processed(payload: dict[str, Any]) -> dict[str, Any]:
    """Acción 'processed': mueve el archivo identificado por 'id' de
    SOURCE_PREFIX al prefijo de procesados.

    ``id`` se decodifica directamente a la ``key`` original (ver
    ``id_codec.py``); no se requiere ningún almacenamiento intermedio para
    resolverlo. ``requestId`` se recibe únicamente para trazabilidad/logging
    y se devuelve igual en la respuesta.

    El prefijo destino es, por defecto, el ``PROCESSED_PREFIX`` configurado
    en la Lambda; el cliente puede elegir uno distinto por llamada enviando
    ``processedPrefix`` en el payload (ver ``validate_processed_payload``).
    """
    request_id, id_token, processed_prefix_override = validate_processed_payload(payload)

    try:
        key = decode_key(id_token)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if not key.startswith(_settings.source_prefix):
        raise ValidationError(
            f"El 'id' recibido no corresponde a un archivo bajo el prefijo de origen configurado "
            f"('{_settings.source_prefix}')."
        )

    processed_prefix = processed_prefix_override or _settings.processed_prefix
    if processed_prefix == _settings.source_prefix:
        raise ValidationError(
            "'processedPrefix' no puede ser igual al prefijo de origen configurado "
            f"('{_settings.source_prefix}')."
        )

    relative_path = key[len(_settings.source_prefix):]
    destination_key = f"{processed_prefix}{relative_path}"

    _s3_service.move_object(key, destination_key)
    log_event(
        logger,
        action="processed",
        status="MOVED",
        request_id=request_id,
        source=key,
        destination=destination_key,
        processed_prefix=processed_prefix,
        processed_prefix_overridden=processed_prefix_override is not None,
    )
    return build_response(200, {"requestId": request_id, "success": True})


_ACTION_HANDLERS = {
    "pending": _handle_pending,
    "processed": _handle_processed,
}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entrypoint de la Lambda.

    Enruta la acción solicitada (`pending`, `processed`) y traduce cualquier
    error a una respuesta HTTP con el código adecuado:
        400 - parámetros faltantes o inválidos
        404 - archivo no encontrado
        500 - error de configuración o error inesperado
    """
    aws_request_id = getattr(context, "aws_request_id", "local")
    action_name = "unknown"

    try:
        if _config_error:
            raise ConfigError(_config_error)

        payload = _extract_payload(event)
        action_name = validate_action(payload)

        log_event(logger, action=action_name, status="RECEIVED", aws_request_id=aws_request_id)

        handler_fn = _ACTION_HANDLERS[action_name]
        return handler_fn(payload)

    except ValidationError as exc:
        log_event(logger, action=action_name, status="VALIDATION_ERROR", detail=str(exc))
        return build_response(400, {"success": False, "error": str(exc)})

    except NotFoundError as exc:
        log_event(logger, action=action_name, status="NOT_FOUND", detail=str(exc))
        return build_response(404, {"success": False, "error": str(exc)})

    except ConfigError as exc:
        logger.error("Error de configuración: %s", exc)
        return build_response(
            500, {"success": False, "error": "Error de configuración del servicio."}
        )

    except Exception:  # noqa: BLE001 - salvaguarda final; se registra el detalle real
        logger.exception(
            "Error inesperado procesando la petición (action=%s, aws_request_id=%s).",
            action_name,
            aws_request_id,
        )
        return build_response(500, {"success": False, "error": "Error interno del servidor."})
