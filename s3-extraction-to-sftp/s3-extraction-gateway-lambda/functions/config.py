"""Configuración del servicio a partir de variables de entorno.

Ninguna credencial de AWS se lee ni se define aquí: la Lambda utiliza
exclusivamente las credenciales temporales inyectadas por su IAM Role en
tiempo de ejecución.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from errors import ConfigError

_ENV_BUCKET_NAME = "BUCKET_NAME"
_ENV_SOURCE_PREFIX = "SOURCE_PREFIX"
_ENV_PROCESSED_PREFIX = "PROCESSED_PREFIX"
_ENV_URL_EXPIRATION_SECONDS = "URL_EXPIRATION_SECONDS"

_DEFAULT_URL_EXPIRATION_SECONDS = 300


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuración inmutable y tipada del servicio.

    Attributes:
        bucket_name: Bucket S3 sobre el que opera la Lambda.
        source_prefix: Prefijo donde llegan los archivos pendientes.
        processed_prefix: Prefijo destino tras marcar un archivo como procesado.
        url_expiration_seconds: Vigencia (segundos) de las URLs prefirmadas.
    """

    bucket_name: str
    source_prefix: str
    processed_prefix: str
    url_expiration_seconds: int


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ConfigError(f"Falta la variable de entorno requerida: '{name}'.")
    return value.strip()


def _normalize_prefix(prefix: str) -> str:
    """Garantiza que un prefijo S3 termine en '/'."""
    prefix = prefix.strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def load_settings() -> Settings:
    """Carga y valida la configuración desde variables de entorno.

    Raises:
        ConfigError: Si falta alguna variable requerida o algún valor es inválido.
    """
    bucket_name = _require(_ENV_BUCKET_NAME)
    source_prefix = _normalize_prefix(_require(_ENV_SOURCE_PREFIX))
    processed_prefix = _normalize_prefix(_require(_ENV_PROCESSED_PREFIX))

    raw_expiration = os.environ.get(
        _ENV_URL_EXPIRATION_SECONDS, str(_DEFAULT_URL_EXPIRATION_SECONDS)
    )
    try:
        url_expiration_seconds = int(raw_expiration)
    except ValueError as exc:
        raise ConfigError(
            f"'{_ENV_URL_EXPIRATION_SECONDS}' debe ser un entero. Valor recibido: '{raw_expiration}'."
        ) from exc
    if url_expiration_seconds <= 0:
        raise ConfigError(
            f"'{_ENV_URL_EXPIRATION_SECONDS}' debe ser mayor que 0. Valor recibido: {url_expiration_seconds}."
        )

    if source_prefix == processed_prefix:
        raise ConfigError(
            f"'{_ENV_SOURCE_PREFIX}' y '{_ENV_PROCESSED_PREFIX}' no pueden ser iguales."
        )

    return Settings(
        bucket_name=bucket_name,
        source_prefix=source_prefix,
        processed_prefix=processed_prefix,
        url_expiration_seconds=url_expiration_seconds,
    )
