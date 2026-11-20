#!/usr/bin/env python3
"""Punto de entrada de consola de S3 Extraction Service.

Ejecuta UNA sola pasada (pending -> por archivo -> fin) y termina. Está
pensado para ser invocado por un CRON (Linux) o el Task Scheduler
(Windows): no implementa polling, servicios de Windows, demonios, timers,
hilos ni ningún proceso residente.

Ejemplo de crontab (cada 5 minutos):
    */5 * * * * /ruta/venv/bin/python /ruta/S3-Extraction-Service/app/main.py

Ejemplo de Task Scheduler (Windows): programar una tarea que ejecute
    C:\\ruta\\venv\\Scripts\\python.exe C:\\ruta\\S3-Extraction-Service\\app\\main.py
con un desencadenador repetido cada 5 minutos.

Configuración: variables de entorno o archivo ``.env`` (ver ``.env.example``).

Código de salida:
    0 - La ejecución terminó correctamente (incluso si algún archivo
        individual falló y quedó pendiente para el siguiente ciclo).
    Distinto de 0 - Ocurrió un error crítico que impidió completar el
        proceso (configuración inválida, API inalcanzable, o no fue
        posible conectar al servidor SFTP).
"""

from __future__ import annotations

import sys
import time

from api_client import ApiClient, ApiError
from config import ConfigError, load_settings
from download_manager import DownloadManager
from logger import build_logger
from service import ExtractionService
from sftp_client import SftpClient, SftpError

EXIT_OK = 0
EXIT_ERROR = 1


def main() -> int:
    """Orquesta la construcción de dependencias y ejecuta una pasada del servicio."""
    start = time.monotonic()

    try:
        settings = load_settings()
    except ConfigError as exc:
        # Sin configuración no hay logger todavía: se reporta a stderr.
        print(f"[CONFIG ERROR] {exc}", file=sys.stderr)
        return EXIT_ERROR

    logger = build_logger("s3_extraction_service", settings.log_file, settings.log_level)
    logger.info("Inicio de ejecución de S3 Extraction Service.")

    try:
        api_client = ApiClient(
            base_url=settings.api_url,
            api_key=settings.api_key,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
            retry_wait_seconds=settings.retry_wait_seconds,
            logger=logger,
        )
        download_manager = DownloadManager(
            download_root=settings.download_path,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
            retry_wait_seconds=settings.retry_wait_seconds,
            logger=logger,
        )
        sftp_client = SftpClient(
            host=settings.sftp_host,
            port=settings.sftp_port,
            username=settings.sftp_user,
            password=settings.sftp_password,
            remote_root=settings.sftp_remote_path,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
            retry_wait_seconds=settings.retry_wait_seconds,
            logger=logger,
        )
        service = ExtractionService(settings, api_client, download_manager, sftp_client, logger)

        summary = service.run()

        elapsed = time.monotonic() - start
        logger.info(
            "Fin de ejecución. Tiempo total=%.2fs | encontrados=%d descargados=%d "
            "subidos=%d confirmados=%d fallidos=%d",
            elapsed,
            summary.files_found,
            summary.files_downloaded,
            summary.files_uploaded,
            summary.files_confirmed,
            summary.files_failed,
        )
        return EXIT_OK

    except (ApiError, SftpError) as exc:
        elapsed = time.monotonic() - start
        logger.error(
            "Error crítico: no se pudo completar el proceso (tiempo=%.2fs): %s",
            elapsed,
            exc,
            exc_info=True,
        )
        return EXIT_ERROR

    except Exception:  # noqa: BLE001 - salvaguarda final; se registra el detalle real
        elapsed = time.monotonic() - start
        logger.exception("Error crítico inesperado (tiempo=%.2fs).", elapsed)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
