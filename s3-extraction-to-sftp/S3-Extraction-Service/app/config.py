"""Configuración del servicio a partir de variables de entorno (archivo .env).

Ninguna credencial de AWS se lee ni se define aquí: la aplicación nunca
habla con S3 directamente, solo con el API Gateway (vía ``x-api-key``) y con
el servidor SFTP destino (vía usuario/contraseña).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """Falta o es inválida una variable de entorno requerida por el servicio."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuración inmutable y tipada, cargada desde variables de entorno.

    Attributes:
        api_url: URL base del API Gateway (sin '/files' al final).
        api_key: API Key para el header ``x-api-key``.
        source_prefix: Prefijo S3 de archivos pendientes (se envía en la
            acción 'pending' y debe coincidir con el configurado en la Lambda).
        download_path: Carpeta local raíz donde se descargan los archivos.
        sftp_host: Host o IP del servidor SFTP destino.
        sftp_port: Puerto SFTP.
        sftp_user: Usuario SFTP.
        sftp_password: Password del usuario SFTP.
        sftp_remote_path: Carpeta remota raíz (ruta absoluta) en el SFTP.
        log_level: Nivel de logging (DEBUG, INFO, WARNING, ERROR).
        log_file: Ruta del archivo de log (con rotación automática).
        max_retries: Reintentos máximos ante errores transitorios.
        retry_wait_seconds: Espera fija entre reintentos, en segundos.
        request_timeout_seconds: Timeout de red (API, descarga, SFTP), en segundos.
        keep_local_files: Si es True, los archivos confirmados como procesados
            se mueven a ``DOWNLOAD_PATH/processed/<relativePath>`` en vez de
            eliminarse. Si es False, se eliminan tras confirmar el procesado.
        strip_relative_prefix: Sub-ruta inicial de ``relativePath`` a
            eliminar antes de construir la ruta local y la ruta SFTP (por
            ejemplo, una carpeta de carga interna que no debe replicarse en
            el destino). Cadena vacía si no aplica ningún recorte.
    """

    api_url: str
    api_key: str
    source_prefix: str
    download_path: Path
    sftp_host: str
    sftp_port: int
    sftp_user: str
    sftp_password: str
    sftp_remote_path: str
    log_level: str
    log_file: Path
    max_retries: int
    retry_wait_seconds: int
    request_timeout_seconds: float
    keep_local_files: bool
    strip_relative_prefix: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ConfigError(f"Falta la variable de entorno requerida: '{name}'.")
    return value.strip()


def _optional_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"'{name}' debe ser un entero. Valor recibido: '{raw}'.") from exc


def _optional_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"'{name}' debe ser numérico. Valor recibido: '{raw}'.") from exc


def _optional_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "si", "sí"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ConfigError(
        f"'{name}' debe ser 'true' o 'false'. Valor recibido: '{raw}'."
    )


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Carga y valida la configuración desde variables de entorno / archivo ``.env``.

    Args:
        env_file: Ruta opcional a un archivo ``.env`` específico. Si es
            ``None``, se busca un archivo ``.env`` en el directorio de
            trabajo actual (comportamiento por defecto de ``python-dotenv``).

    Raises:
        ConfigError: Si falta alguna variable requerida o algún valor es inválido.
    """
    load_dotenv(dotenv_path=env_file)

    api_url = _require("API_URL").rstrip("/")
    api_key = _require("API_KEY")

    source_prefix = _require("SOURCE_PREFIX")
    if not source_prefix.endswith("/"):
        source_prefix += "/"

    download_path = Path(_require("DOWNLOAD_PATH")).expanduser()

    sftp_host = _require("SFTP_HOST")
    sftp_port = _optional_int("SFTP_PORT", 22)
    sftp_user = _require("SFTP_USER")
    sftp_password = _require("SFTP_PASSWORD")
    sftp_remote_path = "/" + _require("SFTP_REMOTE_PATH").strip("/")

    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    log_file = Path(os.environ.get("LOG_FILE", "logs/service.log")).expanduser()

    max_retries = _optional_int("MAX_RETRIES", 3)
    retry_wait_seconds = _optional_int("RETRY_WAIT_SECONDS", 5)
    request_timeout_seconds = _optional_float("REQUEST_TIMEOUT_SECONDS", 30.0)
    keep_local_files = _optional_bool("KEEP_LOCAL_FILES", True)

    strip_relative_prefix = os.environ.get("STRIP_RELATIVE_PREFIX", "").strip().strip("/")
    if strip_relative_prefix:
        strip_relative_prefix += "/"

    if max_retries < 1:
        raise ConfigError("'MAX_RETRIES' debe ser mayor o igual a 1.")
    if retry_wait_seconds < 0:
        raise ConfigError("'RETRY_WAIT_SECONDS' debe ser mayor o igual a 0.")
    if sftp_port <= 0:
        raise ConfigError("'SFTP_PORT' debe ser mayor que 0.")
    if request_timeout_seconds <= 0:
        raise ConfigError("'REQUEST_TIMEOUT_SECONDS' debe ser mayor que 0.")

    return Settings(
        api_url=api_url,
        api_key=api_key,
        source_prefix=source_prefix,
        download_path=download_path,
        sftp_host=sftp_host,
        sftp_port=sftp_port,
        sftp_user=sftp_user,
        sftp_password=sftp_password,
        sftp_remote_path=sftp_remote_path,
        log_level=log_level,
        log_file=log_file,
        max_retries=max_retries,
        retry_wait_seconds=retry_wait_seconds,
        request_timeout_seconds=request_timeout_seconds,
        keep_local_files=keep_local_files,
        strip_relative_prefix=strip_relative_prefix,
    )
