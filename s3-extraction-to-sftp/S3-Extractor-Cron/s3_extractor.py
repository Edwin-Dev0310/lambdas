#!/usr/bin/env python3
"""S3 Extractor - versión para cron.

Ejecuta UNA sola pasada: lista los archivos nuevos en el prefijo de entrada
de S3, los descarga a una carpeta local, verifica la descarga y mueve en S3
el archivo desde el prefijo de entrada al de procesados. Si algo falla, el
archivo se deja intacto en S3 para reintentarse en la siguiente ejecución
del cron.

La programación periódica NO la maneja este script: se agenda vía crontab.
Ejemplo de crontab (cada 5 minutos):
    */5 * * * * /ruta/venv/bin/python /ruta/s3_extractor.py >> /ruta/logs/cron.log 2>&1

Configuración: variables de entorno o archivo .env (ver .env.example).
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

T = TypeVar("T")

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
load_dotenv()

BUCKET = os.environ["S3_BUCKET"]
PREFIX = os.environ["S3_PREFIX"].rstrip("/") + "/"
PROCESSED_PREFIX = os.environ["PROCESSED_PREFIX"].rstrip("/") + "/"
REGION = os.environ["AWS_REGION"]
LOCAL_DOWNLOAD_PATH = Path(os.environ["LOCAL_DOWNLOAD_PATH"]).expanduser()
LOG_FILE = Path(os.environ.get("LOG_FILE", "logs/s3_extractor.log")).expanduser()
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("RETRY_BACKOFF_SECONDS", "2"))

if PREFIX == PROCESSED_PREFIX:
    raise ValueError("S3_PREFIX y PROCESSED_PREFIX no pueden ser iguales.")

LOCAL_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logger = logging.getLogger("s3_extractor")
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)


def log_operation(file_name: str, status: str, detail: str = "") -> None:
    """Registra una operación con fecha/hora (via el formatter), archivo y estado."""
    message = f"archivo='{file_name}' estado='{status}'"
    if detail:
        message += f" detalle='{detail}'"
    if status.upper() in {"FAILED", "ERROR"}:
        logger.error(message)
    else:
        logger.info(message)


# --------------------------------------------------------------------------- #
# Reintentos automáticos (máximo 3 intentos, backoff exponencial)
# --------------------------------------------------------------------------- #
_RETRYABLE_ERROR_CODES = {
    "RequestTimeout", "RequestTimeoutException", "Throttling", "ThrottlingException",
    "SlowDown", "InternalError", "ServiceUnavailable", "500", "503",
}


class S3OperationError(Exception):
    """Error definitivo tras agotar los reintentos (o error no reintentable)."""


def with_retries(func: Callable[..., T]) -> Callable[..., T]:
    """Decorador que reintenta ``func`` ante errores transitorios de AWS.

    Reintenta hasta MAX_RETRIES veces con backoff exponencial
    (RETRY_BACKOFF_SECONDS * 2^intento). Errores no transitorios
    (p. ej. NoSuchKey, AccessDenied) se propagan de inmediato sin reintentar.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")
                if error_code not in _RETRYABLE_ERROR_CODES:
                    raise
                last_exc = exc
            except (BotoCoreError, TimeoutError, OSError) as exc:
                last_exc = exc

            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Intento %s/%s fallo en '%s': %s. Reintentando en %.1fs.",
                    attempt, MAX_RETRIES, func.__name__, last_exc, wait,
                )
                time.sleep(wait)

        raise S3OperationError(
            f"Se agotaron los {MAX_RETRIES} intentos para '{func.__name__}': {last_exc}"
        ) from last_exc

    return wrapper


def get_s3_client():
    return boto3.client("s3", region_name=REGION, config=Config(connect_timeout=10, read_timeout=30))


@with_retries
def list_new_files(s3, bucket: str, prefix: str) -> list[dict[str, Any]]:
    """Lista los archivos (no carpetas) bajo ``prefix``."""
    objects: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/") and obj.get("Size", 0) == 0:
                continue
            objects.append(obj)
    return objects


@with_retries
def download_file(s3, bucket: str, key: str, local_path: Path) -> None:
    """Descarga ``key`` a un archivo temporal y lo renombra al destino final."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = local_path.with_suffix(local_path.suffix + ".part")
    s3.download_file(bucket, key, str(tmp_path))
    tmp_path.replace(local_path)


@with_retries
def move_object(s3, bucket: str, source_key: str, destination_key: str) -> None:
    """Mueve un objeto dentro del mismo bucket (copy + delete)."""
    s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source_key}, Key=destination_key)
    s3.delete_object(Bucket=bucket, Key=source_key)


def verify_download(local_path: Path, expected_size: int) -> bool:
    """Verifica que el archivo descargado tenga el tamaño esperado."""
    return local_path.exists() and local_path.stat().st_size == expected_size


# --------------------------------------------------------------------------- #
# Proceso principal (una sola pasada; la periodicidad la da el crontab)
# --------------------------------------------------------------------------- #
def process_file(s3, obj: dict[str, Any]) -> None:
    key = obj["Key"]
    expected_size = int(obj.get("Size", 0))
    relative_name = key[len(PREFIX):] if key.startswith(PREFIX) else Path(key).name
    if not relative_name:
        return

    local_path = LOCAL_DOWNLOAD_PATH / relative_name

    try:
        # Si ya existe localmente con el tamaño correcto (p. ej. una
        # ejecución anterior se cortó antes de mover el objeto en S3),
        # se evita re-descargar y solo se reintenta el movimiento.
        if not (local_path.exists() and local_path.stat().st_size == expected_size):
            download_file(s3, BUCKET, key, local_path)
            log_operation(key, "DOWNLOADED")

        if not verify_download(local_path, expected_size):
            local_path.unlink(missing_ok=True)
            raise S3OperationError(f"Verificación de integridad fallida (tamaño != {expected_size}).")
        log_operation(key, "VERIFIED")

        destination_key = PROCESSED_PREFIX + relative_name
        move_object(s3, BUCKET, key, destination_key)
        log_operation(key, "MOVED", detail=f"destino={destination_key}")

    except Exception as exc:  # noqa: BLE001
        # Se deja el archivo intacto en S3 para reintentar en la siguiente
        # ejecución del cron.
        log_operation(key, "FAILED", detail=str(exc))


def main() -> int:
    s3 = get_s3_client()
    try:
        objects = list_new_files(s3, BUCKET, PREFIX)
    except S3OperationError as exc:
        log_operation(PREFIX, "FAILED", detail=str(exc))
        return 1

    if not objects:
        logger.info("Sin archivos nuevos en s3://%s/%s", BUCKET, PREFIX)
        return 0

    for obj in objects:
        process_file(s3, obj)

    return 0


if __name__ == "__main__":
    sys.exit(main())
