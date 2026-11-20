"""Cliente SFTP: entrega los archivos descargados al servidor destino,
conservando la estructura de carpetas indicada por ``relativePath``.

La conexión se abre una sola vez por ejecución (no una por archivo) y se
reutiliza para todo el lote: es más eficiente que reconectar en cada
archivo y sigue cumpliendo el flujo funcional requerido (conectar, crear
estructura remota, subir, verificar) para cada uno de ellos.
"""

from __future__ import annotations

import logging
import posixpath
from pathlib import Path
from types import TracebackType

import paramiko
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_fixed

_TRANSIENT_EXCEPTIONS = (paramiko.SSHException, OSError, EOFError)


class SftpError(Exception):
    """Error irrecuperable en una operación SFTP (conexión o transferencia)."""


class SftpClient:
    """Encapsula la conexión SFTP y las operaciones de entrega de archivos.

    Puede usarse como gestor de contexto para garantizar el cierre de la
    conexión incluso si ocurre un error a mitad del lote::

        with SftpClient(...) as sftp:
            sftp.upload(local_path, relative_path)
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        remote_root: str,
        timeout_seconds: float,
        max_retries: int,
        retry_wait_seconds: int,
        logger: logging.Logger,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._remote_root = remote_root.rstrip("/") or "/"
        self._timeout = timeout_seconds
        self._logger = logger
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._known_remote_dirs: set[str] = set()
        self._retry = retry(
            stop=stop_after_attempt(max_retries),
            wait=wait_fixed(retry_wait_seconds),
            retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
            reraise=True,
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )

    def connect(self) -> None:
        """Abre la conexión SFTP (con reintentos ante errores transitorios).

        Raises:
            SftpError: Si se agotan los reintentos sin lograr conectar.
        """

        def _do_connect() -> None:
            transport = paramiko.Transport((self._host, self._port))
            transport.banner_timeout = self._timeout
            transport.connect(username=self._username, password=self._password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.get_channel().settimeout(self._timeout)
            self._transport = transport
            self._sftp = sftp

        try:
            self._retry(_do_connect)()
        except _TRANSIENT_EXCEPTIONS as exc:
            raise SftpError(
                f"No se pudo conectar al servidor SFTP {self._host}:{self._port}: {exc}"
            ) from exc

        self._logger.info("Conexión SFTP establecida con %s:%s.", self._host, self._port)

    def close(self) -> None:
        """Cierra la conexión SFTP si está abierta. Segura de llamar más de una vez."""
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def __enter__(self) -> "SftpClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def _require_connection(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise SftpError("No hay una conexión SFTP activa; llame a connect() primero.")
        return self._sftp

    def _ensure_remote_dir(self, remote_dir: str) -> None:
        """Crea recursivamente ``remote_dir`` si no existe (equivalente a 'mkdir -p')."""
        sftp = self._require_connection()

        if remote_dir in self._known_remote_dirs:
            return

        current = ""
        for part in (p for p in remote_dir.strip("/").split("/") if p):
            current = f"{current}/{part}"
            if current in self._known_remote_dirs:
                continue
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)
            self._known_remote_dirs.add(current)

    def upload(self, local_path: Path, relative_path: str, expected_size: int) -> str:
        """Sube ``local_path`` a ``SFTP_REMOTE_PATH/relative_path`` y confirma
        en el propio servidor que el archivo quedó bien escrito.

        Crea automáticamente la estructura de carpetas remota a partir de
        ``relative_path``. Sube con sufijo ``.part`` y renombra al
        finalizar, para no dejar archivos a medio subir visibles en la
        carpeta remota. Si ya existe un archivo remoto con el nombre final
        (por ejemplo, un reintento tras una caída de red posterior a la
        subida), se reemplaza.

        A diferencia de solo confiar en que ``put()`` no lanzó una
        excepción, aquí se registra la respuesta real del servidor en cada
        paso (bytes escritos por ``put()``, y tamaño confirmado con
        ``stat()`` tras el rename) y se lanza ``SftpError`` de inmediato si
        el archivo no aparece en destino o su tamaño no coincide — así el
        log deja evidencia explícita de si el SFTP realmente recibió el
        archivo, en vez de asumir éxito solo porque no hubo una excepción de
        conexión.

        Args:
            local_path: Ruta del archivo local ya descargado y verificado.
            relative_path: Ruta relativa (ver ``download_manager.build_local_path``),
                usada para replicar la misma estructura de carpetas en el SFTP.
            expected_size: Tamaño en bytes que debe tener el archivo en el
                servidor SFTP tras la subida (el mismo que informó el API).

        Returns:
            La ruta remota final del archivo subido.

        Raises:
            SftpError: Si el archivo no aparece en destino tras subir y
                renombrar, o si su tamaño remoto no coincide con ``expected_size``.
        """
        remote_path = posixpath.join(self._remote_root, relative_path)
        remote_dir = posixpath.dirname(remote_path)
        remote_tmp_path = f"{remote_path}.part"

        def _do_upload() -> None:
            sftp = self._require_connection()
            self._ensure_remote_dir(remote_dir)

            put_attrs = sftp.put(str(local_path), remote_tmp_path)
            self._logger.info(
                "SFTP put() OK: '%s' (%d bytes escritos en el temporal).",
                remote_tmp_path,
                put_attrs.st_size,
            )

            try:
                sftp.remove(remote_path)
            except OSError:
                pass  # No existía aún; es el caso normal.
            sftp.rename(remote_tmp_path, remote_path)

            try:
                final_attrs = sftp.stat(remote_path)
            except FileNotFoundError as exc:
                raise SftpError(
                    f"El archivo no aparece en el servidor SFTP tras subir y renombrar: "
                    f"'{remote_path}'. Verifique permisos de escritura y la ruta real "
                    f"(algunos servidores SFTP aplican chroot al usuario, y la ruta "
                    f"absoluta que ve este cliente puede no coincidir con la que ve al "
                    f"navegar por otro medio)."
                ) from exc

            if final_attrs.st_size != expected_size:
                raise SftpError(
                    f"Tamaño remoto confirmado por el SFTP ({final_attrs.st_size} bytes) "
                    f"no coincide con el esperado ({expected_size} bytes) en '{remote_path}'."
                )

            self._logger.info(
                "SFTP confirmó el archivo en destino: '%s' (%d bytes).",
                remote_path,
                final_attrs.st_size,
            )

        self._retry(_do_upload)()
        return remote_path
