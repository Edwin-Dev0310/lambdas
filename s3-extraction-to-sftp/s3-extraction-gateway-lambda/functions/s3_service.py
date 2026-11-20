"""Operaciones sobre S3: listar archivos pendientes (con URL prefirmada
embebida), verificar existencia y mover objetos.

Toda la lógica de acceso a S3 vive en esta clase para mantener ``handler.py``
enfocado exclusivamente en el enrutamiento HTTP y la traducción de errores.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import boto3
from botocore.exceptions import ClientError

from errors import NotFoundError
from id_codec import encode_key

_NOT_FOUND_ERROR_CODES = {"404", "NoSuchKey", "NotFound"}


def _format_last_modified(value: datetime) -> str:
    """Formatea ``LastModified`` como ISO 8601 en UTC con sufijo 'Z'."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class S3Service:
    """Encapsula las operaciones S3 requeridas por el servicio de extracción."""

    def __init__(self, bucket_name: str, logger: logging.Logger) -> None:
        self._bucket = bucket_name
        self._logger = logger
        self._client = boto3.client("s3")

    def list_pending_files(self, prefix: str, url_expiration_seconds: int) -> list[dict[str, Any]]:
        """Lista todos los archivos bajo ``prefix`` (acción 'pending').

        Para cada objeto genera de inmediato su URL prefirmada de descarga
        (el objeto ya se sabe existente por venir del propio listado, por lo
        que no hace falta una verificación adicional con ``head_object``).

        Excluye marcadores de "carpeta" (keys que terminan en '/' con tamaño
        0) y ordena el resultado por ``lastModified`` ascendente.
        """
        results: list[dict[str, Any]] = []
        paginator = self._client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/") and obj.get("Size", 0) == 0:
                    continue

                last_modified: datetime = obj["LastModified"]
                download_url = self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=url_expiration_seconds,
                )

                results.append(
                    {
                        "id": encode_key(key),
                        "key": key,
                        "relativePath": key[len(prefix):],
                        "fileName": PurePosixPath(key).name,
                        "size": obj.get("Size", 0),
                        "lastModified": _format_last_modified(last_modified),
                        "downloadUrl": download_url,
                    }
                )

        results.sort(key=lambda item: item["lastModified"])
        return results

    def object_exists(self, key: str) -> bool:
        """Indica si ``key`` existe en el bucket configurado."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _NOT_FOUND_ERROR_CODES:
                return False
            raise

    def move_object(self, source_key: str, destination_key: str) -> None:
        """Mueve un objeto (copy + delete) conservando la estructura de carpetas.

        Verifica que el origen exista antes de copiar y que el destino exista
        tras la copia antes de eliminar el original. Si la verificación del
        destino falla, el original se conserva intacto.

        Raises:
            NotFoundError: Si el objeto origen no existe.
            RuntimeError: Si la copia no puede verificarse en el destino.
        """
        if not self.object_exists(source_key):
            raise NotFoundError(f"El archivo '{source_key}' no existe en el bucket.")

        self._client.copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": source_key},
            Key=destination_key,
        )

        if not self.object_exists(destination_key):
            raise RuntimeError(
                f"No se pudo verificar la copia en '{destination_key}'; "
                "se conserva el archivo original."
            )

        self._client.delete_object(Bucket=self._bucket, Key=source_key)
        self._logger.info(
            "Objeto movido correctamente: '%s' -> '%s'", source_key, destination_key
        )
