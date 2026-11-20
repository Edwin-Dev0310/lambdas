"""Orquestación del ciclo de extracción: una sola pasada, sin loop propio.

La periodicidad (cada 5 minutos) la controla el CRON/Task Scheduler que
invoca ``main.py``; esta clase solo sabe ejecutar un ciclo completo:
'pending' -> por cada archivo (descargar, verificar, subir por SFTP,
verificar, confirmar) -> fin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from api_client import ApiClient, PendingFile
from config import Settings
from download_manager import DownloadManager
from sftp_client import SftpClient


@dataclass
class ExecutionSummary:
    """Resumen numérico de una ejecución completa del servicio."""

    files_found: int = 0
    files_downloaded: int = 0
    files_uploaded: int = 0
    files_confirmed: int = 0
    files_failed: int = 0

    @property
    def had_failures(self) -> bool:
        """True si al menos un archivo no se pudo procesar por completo."""
        return self.files_failed > 0


class ExtractionService:
    """Coordina el API, la descarga y el SFTP para procesar el lote pendiente.

    Todas las dependencias (``ApiClient``, ``DownloadManager``,
    ``SftpClient``) se inyectan por constructor: esta clase no crea ninguna
    de ellas ni conoce sus detalles de implementación, lo que la hace fácil
    de probar con dobles de prueba (mocks/stubs) y de extender a futuro
    (por ejemplo, agregando un nuevo destino de entrega).
    """

    def __init__(
        self,
        settings: Settings,
        api_client: ApiClient,
        download_manager: DownloadManager,
        sftp_client: SftpClient,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._api_client = api_client
        self._download_manager = download_manager
        self._sftp_client = sftp_client
        self._logger = logger

    def run(self) -> ExecutionSummary:
        """Ejecuta una pasada completa y retorna un resumen numérico.

        Solo propaga excepciones que impiden completar el proceso por
        completo: fallar al consultar 'pending' o fallar al conectar el
        SFTP (sin conexión, ningún archivo puede entregarse). Los errores
        de archivos individuales se registran y NO detienen el ciclo: se
        continúa con el siguiente archivo (ver ``_process_file``).

        Raises:
            ApiError: Si se agotan los reintentos consultando 'pending'.
            SftpError: Si se agotan los reintentos conectando al SFTP.
        """
        summary = ExecutionSummary()

        request_id, files = self._api_client.get_pending_files(self._settings.source_prefix)
        summary.files_found = len(files)
        self._logger.info(
            "Archivos pendientes encontrados: %d (requestId=%s).", len(files), request_id
        )

        if not files:
            return summary

        self._sftp_client.connect()
        try:
            for pending_file in files:
                if self._process_file(pending_file, request_id, summary):
                    summary.files_confirmed += 1
                else:
                    summary.files_failed += 1
        finally:
            self._sftp_client.close()

        return summary

    def _process_file(
        self, pending_file: PendingFile, request_id: str, summary: ExecutionSummary
    ) -> bool:
        """Procesa un archivo: descargar -> verificar -> subir -> verificar -> confirmar.

        Retorna ``True`` si todo el flujo fue exitoso. Ante *cualquier*
        error se registra (con stacktrace) y se retorna ``False``: el
        archivo queda intacto en S3 (nunca se confirma 'processed') para
        reintentarse en la siguiente ejecución del cron.
        """
        relative_path = self._effective_relative_path(pending_file.relative_path)
        local_path = self._download_manager.build_local_path(relative_path)

        try:
            self._download_manager.download(pending_file.download_url, local_path)
            summary.files_downloaded += 1
            self._logger.info("Descargado: '%s' -> '%s'.", pending_file.key, local_path)

            if not self._download_manager.verify_size(local_path, pending_file.size):
                self._download_manager.discard(local_path)
                raise RuntimeError(
                    f"Tamaño local no coincide con el informado por la API "
                    f"(esperado={pending_file.size})."
                )

            # upload() confirma con el propio servidor SFTP (put + stat tras
            # el rename) que el archivo quedó bien escrito, con el tamaño
            # esperado; lanza SftpError de inmediato si no es así.
            remote_path = self._sftp_client.upload(local_path, relative_path, pending_file.size)
            summary.files_uploaded += 1
            self._logger.info("Subido y confirmado por SFTP: '%s' -> '%s'.", pending_file.key, remote_path)

            self._api_client.confirm_processed(request_id, pending_file.id)
            self._logger.info("Procesamiento confirmado: '%s'.", pending_file.key)

            if self._settings.keep_local_files:
                # Se conserva como respaldo/auditoría local en vez de eliminarse.
                processed_path = self._download_manager.move_to_processed(local_path, relative_path)
                self._logger.info("Movido a procesados localmente: '%s'.", processed_path)
            else:
                self._download_manager.discard(local_path)
                self._logger.info("Archivo local eliminado tras confirmar procesamiento: '%s'.", local_path)

            return True

        except Exception as exc:  # noqa: BLE001 - se aísla el fallo de un archivo del resto del lote
            self._logger.error(
                "Fallo procesando '%s': %s", pending_file.key, exc, exc_info=True
            )
            return False

    def _effective_relative_path(self, relative_path: str) -> str:
        """Aplica ``STRIP_RELATIVE_PREFIX`` a ``relativePath`` antes de usarlo
        para construir la ruta local y la ruta remota en el SFTP.

        Sirve para eliminar una sub-ruta inicial que la API sí necesita
        conservar en ``relativePath`` (por ejemplo, una carpeta interna de
        carga) pero que no debe replicarse en el destino local ni en el
        SFTP. Si ``relative_path`` no comienza con el prefijo configurado,
        se retorna sin cambios (no es un error: evita romper archivos que no
        tengan esa sub-ruta).
        """
        strip_prefix = self._settings.strip_relative_prefix
        if strip_prefix and relative_path.startswith(strip_prefix):
            trimmed = relative_path[len(strip_prefix):]
            self._logger.debug(
                "STRIP_RELATIVE_PREFIX aplicado: '%s' -> '%s'.", relative_path, trimmed
            )
            return trimmed
        return relative_path
