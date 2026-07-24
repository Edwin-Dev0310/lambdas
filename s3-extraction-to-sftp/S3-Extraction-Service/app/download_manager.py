"""Descarga de archivos mediante URLs prefirmadas, conservando la estructura
de carpetas indicada por ``relativePath``.

Esta es una petición HTTPS directa a la URL prefirmada (la firma va en la
URL); no requiere la API Key ni pasa por API Gateway/Lambda.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_fixed

_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
)

_PROCESSED_SUBDIR = "processed"


class DownloadManager:
    """Descarga archivos por HTTPS y valida su integridad por tamaño."""

    def __init__(
        self,
        download_root: Path,
        timeout_seconds: float,
        max_retries: int,
        retry_wait_seconds: int,
        logger: logging.Logger,
    ) -> None:
        self._download_root = download_root
        self._timeout = timeout_seconds
        self._logger = logger
        self._retry = retry(
            stop=stop_after_attempt(max_retries),
            wait=wait_fixed(retry_wait_seconds),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            reraise=True,
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )

    def build_local_path(self, relative_path: str) -> Path:
        """Calcula la ruta local para ``relative_path`` y crea las carpetas necesarias.

        Ejemplo: con ``DOWNLOAD_PATH=C:/Temp/Downloads`` y
        ``relative_path='2026/07/15/xml/factura001.xml'`` el resultado es
        ``C:/Temp/Downloads/2026/07/15/xml/factura001.xml``.
        """
        local_path = self._download_root / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return local_path

    def download(self, download_url: str, local_path: Path) -> None:
        """Descarga ``download_url`` a ``local_path`` (con reintentos automáticos).

        Descarga primero a un archivo temporal con sufijo ``.part`` y
        renombra al finalizar, para no dejar descargas incompletas visibles
        con el nombre final si el proceso se interrumpe a mitad de camino.

        Raises:
            requests.exceptions.RequestException: Si se agotan los
                reintentos ante un error transitorio.
        """

        def _do_download() -> None:
            tmp_path = local_path.with_suffix(local_path.suffix + ".part")
            with requests.get(download_url, stream=True, timeout=self._timeout) as response:
                response.raise_for_status()
                with open(tmp_path, "wb") as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            fh.write(chunk)
            tmp_path.replace(local_path)

        self._retry(_do_download)()

    @staticmethod
    def verify_size(local_path: Path, expected_size: int) -> bool:
        """Verifica que el tamaño local coincida con ``expected_size``."""
        return local_path.exists() and local_path.stat().st_size == expected_size

    def move_to_processed(self, local_path: Path, relative_path: str) -> Path:
        """Mueve ``local_path`` a la carpeta local de procesados, conservando
        la misma estructura de ``relative_path``.

        En vez de eliminar el archivo local tras confirmar el procesamiento
        con el API, se conserva como respaldo/auditoría en
        ``DOWNLOAD_PATH/processed/<relative_path>``. Ejemplo: con
        ``DOWNLOAD_PATH=C:/Temp/Downloads`` y
        ``relative_path='2026/07/15/xml/factura001.xml'`` el resultado es
        ``C:/Temp/Downloads/processed/2026/07/15/xml/factura001.xml``.

        Si ya existe un archivo con ese nombre en destino (por ejemplo, un
        reintento tras una caída a mitad del movimiento), se reemplaza.

        Returns:
            La ruta local final del archivo movido.
        """
        processed_path = self._download_root / _PROCESSED_SUBDIR / relative_path
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.replace(processed_path)
        return processed_path

    @staticmethod
    def discard(local_path: Path) -> None:
        """Elimina ``local_path`` si existe.

        Se usa en dos casos: (1) al descartar una descarga corrupta o
        incompleta (el tamaño no coincide con el informado por la API), y
        (2) al eliminar un archivo ya confirmado como procesado cuando
        ``KEEP_LOCAL_FILES=false`` (por defecto es ``true``: se conserva y
        se mueve a la carpeta local de procesados, ver ``move_to_processed``).
        """
        local_path.unlink(missing_ok=True)
