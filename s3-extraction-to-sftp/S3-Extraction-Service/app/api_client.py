"""Cliente HTTP del API Gateway (S3 Extraction Gateway Lambda).

Toda la comunicación es HTTPS, autenticada con el header ``x-api-key``. Este
cliente nunca usa credenciales de AWS: solo conoce la URL del API y la API
Key. Las descargas reales de S3 ocurren únicamente a través de las URLs
prefirmadas que devuelve la acción 'pending' (ver ``download_manager.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_fixed,
)


class ApiError(Exception):
    """Error definitivo devuelto por el API (4xx de negocio: parámetros
    inválidos, archivo no encontrado, etc.). No se reintenta."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


def _is_transient(exc: BaseException) -> bool:
    """True si ``exc`` amerita reintento: timeouts, problemas de conexión,
    o respuestas 5xx del API. Los errores de negocio 4xx nunca se reintentan."""
    if isinstance(exc, ApiError):
        return exc.status_code >= 500
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


@dataclass(frozen=True, slots=True)
class PendingFile:
    """Representa un archivo pendiente devuelto por la acción 'pending'."""

    id: str
    key: str
    relative_path: str
    file_name: str
    size: int
    last_modified: str
    download_url: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PendingFile":
        """Construye un ``PendingFile`` a partir del JSON devuelto por el API."""
        return PendingFile(
            id=data["id"],
            key=data["key"],
            relative_path=data["relativePath"],
            file_name=data["fileName"],
            size=int(data.get("size", 0)),
            last_modified=data.get("lastModified", ""),
            download_url=data["downloadUrl"],
        )


class ApiClient:
    """Cliente del API REST expuesto por API Gateway + Lambda.

    Encapsula las dos acciones del contrato ('pending' y 'processed') y los
    reintentos automáticos ante errores transitorios de red o del servidor.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        max_retries: int,
        retry_wait_seconds: int,
        logger: logging.Logger,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._logger = logger
        self._retry = retry(
            stop=stop_after_attempt(max_retries),
            wait=wait_fixed(retry_wait_seconds),
            retry=retry_if_exception(_is_transient),
            reraise=True,
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST genérico contra ``{API_URL}/files``, con reintentos automáticos."""

        def _do_post() -> dict[str, Any]:
            response = requests.post(
                f"{self._base_url}/files",
                json=payload,
                headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
            try:
                data = response.json()
            except ValueError:
                data = {"success": False, "error": response.text}

            if response.status_code >= 400:
                raise ApiError(response.status_code, data.get("error", "Error desconocido del API."))

            return data

        return self._retry(_do_post)()

    def get_pending_files(self, prefix: str) -> tuple[str, list[PendingFile]]:
        """Acción 'pending': archivos pendientes bajo ``prefix``.

        Returns:
            Tupla ``(request_id, files)``.
        """
        data = self._post({"action": "pending", "prefix": prefix})
        request_id = str(data.get("requestId", ""))
        files = [PendingFile.from_dict(item) for item in data.get("files", [])]
        return request_id, files

    def confirm_processed(self, request_id: str, file_id: str) -> None:
        """Acción 'processed': confirma que el archivo ``file_id`` fue procesado.

        Raises:
            ApiError: Si el API responde con un error de negocio (400/404)
                o se agotan los reintentos ante un error del servidor (5xx).
        """
        self._post({"action": "processed", "requestId": request_id, "id": file_id})
