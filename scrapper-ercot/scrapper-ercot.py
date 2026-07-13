import io
import json
import logging
import re
import socket
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

# ---------------------------------------------------------------------------
# Imports de terceros  (ver requirements.txt)
# ---------------------------------------------------------------------------
import pandas as pd
import paramiko
import requests
import yaml
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

# ===========================================================================
# CONFIGURACIÓN
# ===========================================================================
CONFIG_FILE = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ===========================================================================
# LOGGER
# ===========================================================================

def setup_logger(log_file: str) -> logging.Logger:
    logger = logging.getLogger("ercot-scraper")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# ===========================================================================
# ESTADO  (anti-duplicados / idempotencia)
# ===========================================================================

def load_state(state_file: str) -> dict:
    path = Path(state_file)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state_file: str, file_name: str) -> None:
    state = {
        "last_processed": file_name,
        "processed_at": datetime.now().isoformat(),
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ===========================================================================
# SCRAPING  (Selenium + BeautifulSoup)
# ===========================================================================

def _build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=options)


def get_file_list(url: str, logger: logging.Logger) -> list[dict]:
    """
    Abre la URL con Chrome headless y espera a que JS pueble la tabla.
    Retorna lista de filas como dicts con clave "_url" para la descarga.
    """
    logger.info("Scraping: %s", url)
    driver = _build_driver()

    try:
        driver.get(url)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState;") == "complete"
        )

        try:
            WebDriverWait(driver, 30).until(
                lambda d: d.execute_script(
                    "return document.querySelectorAll"
                    "('table.report-table tbody tr').length > 0;"
                )
            )
        except TimeoutException:
            raise RuntimeError(
                "Timeout (30 s): la tabla 'report-table' no cargó filas."
            )

        html = driver.page_source

    finally:
        driver.quit()

    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", class_="report-table")
    if not table:
        raise RuntimeError("No se encontró tabla 'report-table' en el HTML.")

    headers: list[str] = []
    thead = table.find("thead")
    if thead and (hr := thead.find("tr")):
        headers = [
            re.sub(r"\s+", " ", th.get_text()).strip()
            for th in hr.find_all(["th", "td"])
        ]

    if not headers:
        raise RuntimeError("La tabla no tiene cabeceras legibles.")

    rows: list[dict] = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue

        row: dict = {
            (headers[i] if i < len(headers) else f"col_{i}"): cell.get_text(strip=True)
            for i, cell in enumerate(cells)
        }

        link = tr.find("a", href=True)
        row["_url"] = urljoin(url, link["href"]) if link else ""
        rows.append(row)

    logger.info("Tabla: %d filas encontradas.", len(rows))
    return rows


def get_latest_file(
    rows: list[dict], file_pattern: str, logger: logging.Logger
) -> dict | None:
    """
    Busca en todos los valores de la fila el patrón ERCOT con fecha/hora.
    Retorna la fila más reciente o None si no hay coincidencias.
    """
    dt_re = re.compile(
        r"_(\d{8})_(\d{6})_[^_]*" + re.escape(file_pattern) + r"[^_]*$",
        re.IGNORECASE,
    )

    candidates: list[dict] = []
    for row in rows:
        matched_name = ""
        for val in row.values():
            if isinstance(val, str) and dt_re.search(val):
                matched_name = val
                break

        if not matched_name:
            continue

        m = dt_re.search(matched_name)
        try:
            dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue

        candidates.append({**row, "_friendly_name": matched_name, "_dt": dt})

    if not candidates:
        logger.warning("Sin archivos con patrón '%s'.", file_pattern)
        return None

    latest = max(candidates, key=lambda x: x["_dt"])
    logger.info("Archivo más reciente: %s", latest["_friendly_name"])
    return latest


# ===========================================================================
# DESCARGA Y EXTRACCIÓN EN MEMORIA
# ===========================================================================

def download_and_extract(url: str, logger: logging.Logger) -> tuple[bytes, str]:
    """
    Descarga el ZIP en memoria (BytesIO) y extrae el primer Excel o CSV.
    No escribe archivos temporales al disco.
    """
    logger.info("Descargando ZIP...")

    response = requests.get(url, timeout=60, stream=True)
    response.raise_for_status()

    zip_buffer = io.BytesIO()
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        zip_buffer.write(chunk)
        total += len(chunk)

    zip_buffer.seek(0)

    with zipfile.ZipFile(zip_buffer) as z:
        names = z.namelist()

        target = next(
            (n for n in names if n.lower().endswith((".xlsx", ".xls"))),
            next((n for n in names if n.lower().endswith(".csv")), None),
        )

        if not target:
            raise RuntimeError("El ZIP no contiene Excel ni CSV.")

        file_bytes = z.read(target)

    logger.info("ZIP: %.1f KB → extraído '%s'", total / 1024, target)
    return file_bytes, target


# ===========================================================================
# LECTURA DEL ARCHIVO
# ===========================================================================

def read_file(file_bytes: bytes, file_name: str, logger: logging.Logger) -> pd.DataFrame:
    """Lee Excel o CSV desde bytes en memoria con pandas."""
    buf = io.BytesIO(file_bytes)
    ext = Path(file_name).suffix.lower()

    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(buf, dtype=str, engine="openpyxl")
    else:
        df = pd.read_csv(buf, dtype=str)

    logger.info("Archivo leído: %d filas × %d columnas.", len(df), len(df.columns))
    return df


# ===========================================================================
# FILTRADO DE REGISTROS
# ===========================================================================

def filter_records(
    df: pd.DataFrame, cfg_filter: dict, logger: logging.Logger
) -> pd.DataFrame:
    """
    Filtra el DataFrame según match_type: equals | contains | list.
    Lanza ValueError si la columna no existe.
    """
    col = cfg_filter["column"]
    values = cfg_filter["values"]
    match_type = cfg_filter.get("match_type", "list")

    if col not in df.columns:
        raise ValueError(
            "Columna '%s' no encontrada. Disponibles: %s" % (col, list(df.columns))
        )

    col_series = df[col].astype(str).str.strip()

    if match_type == "equals":
        mask = col_series == str(values[0]).strip()
    elif match_type == "contains":
        mask = col_series.str.contains(str(values[0]), na=False, case=False)
    else:
        mask = col_series.isin([str(v).strip() for v in values])

    result = df[mask].reset_index(drop=True)
    logger.info("Filtro: %d/%d registros encontrados.", len(result), len(df))
    return result


# ===========================================================================
# ARCHIVO CSV DE SALIDA
# ===========================================================================

def save_output_csv(
    df: pd.DataFrame, cfg_output: dict, logger: logging.Logger
) -> Path:
    """Guarda el DataFrame filtrado como CSV en la carpeta de salida."""
    folder = Path(cfg_output.get("folder", "output"))
    folder.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    filename = cfg_output.get("filename_format", "ERCOT_{datetime}.csv").replace(
        "{datetime}", ts
    )
    output_path = folder / filename

    df.to_csv(output_path, index=False, encoding="utf-8")
    logger.info("CSV generado: %s (%d filas)", output_path, len(df))
    return output_path


# ===========================================================================
# SUBIDA SFTP
# ===========================================================================

def _sftp_mkdir_p(sftp, remote_dir: str, logger: logging.Logger) -> None:
    """Crea el directorio remoto nivel por nivel (mkdir -p)."""
    parts = [p for p in remote_dir.split("/") if p]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)
            logger.info("SFTP dir creado: %s", current)


def upload_sftp(
    file_path: Path, host: str, port: int, user: str,
    password: str, private_key: str, remote_dir: str,
    create_dir: bool, logger: logging.Logger,
) -> None:
    """Transfiere el archivo por SFTP y valida integridad por tamaño."""
    try:
        with socket.create_connection((host, port), timeout=20):
            pass
    except Exception as exc:
        raise RuntimeError(
            "No se puede alcanzar %s:%d. Detalle: %s" % (host, port, exc)
        )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = {"hostname": host, "port": port, "username": user, "timeout": 30}
    if private_key:
        connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key_file(private_key)
    else:
        connect_kwargs["password"] = password

    sftp = None
    try:
        client.connect(**connect_kwargs)
        sftp = client.open_sftp()

        if create_dir:
            _sftp_mkdir_p(sftp, remote_dir, logger)

        remote_path = remote_dir.rstrip("/") + "/" + file_path.name
        sftp.put(str(file_path), remote_path)

        remote_size = sftp.stat(remote_path).st_size
        local_size = file_path.stat().st_size
        if remote_size != local_size:
            raise RuntimeError(
                "Tamaño inválido: local=%d, remoto=%d bytes." % (local_size, remote_size)
            )

        logger.info("SFTP OK: %s → %s (%d bytes)", file_path.name, remote_path, local_size)

    finally:
        if sftp:
            sftp.close()
        client.close()


# ===========================================================================
# ORQUESTADOR PRINCIPAL
# ===========================================================================

def main() -> None:
    start_time = time.time()

    cfg = load_config()
    logger = setup_logger(cfg.get("log_file", "scraper.log"))

    bitacora: dict = {
        "fecha_ejecucion": datetime.now().isoformat(),
        "archivo_descargado": None,
        "archivo_procesado": None,
        "registros_leidos": 0,
        "registros_filtrados": 0,
        "resultado_sftp": "OMITIDO",
        "tiempo_total_seg": 0.0,
        "errores": [],
    }

    logger.info("INICIO scraper ERCOT")

    try:
        state_file = cfg.get("state_file", "state.json")
        state = load_state(state_file)
        last_processed = state.get("last_processed", "")
        logger.info("Último procesado: %s", last_processed or "(ninguno)")

        rows = get_file_list(cfg["ercot"]["url"], logger)

        file_pattern = cfg["ercot"].get("file_pattern", "csv")
        latest = get_latest_file(rows, file_pattern, logger)
        if not latest:
            logger.error("Sin archivo con patrón '%s'. Fin.", file_pattern)
            return

        friendly_name = latest["_friendly_name"]
        download_url = latest.get("_url", "")
        bitacora["archivo_descargado"] = friendly_name

        if friendly_name == last_processed:
            logger.info("Sin novedades — '%s' ya procesado.", friendly_name)
            return

        if not download_url:
            raise RuntimeError("Sin URL de descarga para '%s'." % friendly_name)

        file_bytes, file_name = download_and_extract(download_url, logger)
        bitacora["archivo_procesado"] = file_name

        df_raw = read_file(file_bytes, file_name, logger)
        bitacora["registros_leidos"] = len(df_raw)

        df_filtered = filter_records(df_raw, cfg["filter"], logger)
        bitacora["registros_filtrados"] = len(df_filtered)

        if df_filtered.empty:
            logger.warning("Sin registros que coincidan con el filtro.")

        output_path = None
        if not df_filtered.empty:
            output_path = save_output_csv(df_filtered, cfg["output"], logger)

        sftp_cfg = cfg.get("sftp", {})
        if sftp_cfg.get("host") and output_path:
            try:
                upload_sftp(
                    output_path,
                    host=sftp_cfg["host"],
                    port=int(sftp_cfg.get("port", 22)),
                    user=sftp_cfg["user"],
                    password=sftp_cfg.get("password", ""),
                    private_key=sftp_cfg.get("private_key", ""),
                    remote_dir=sftp_cfg.get("remote_dir", "/"),
                    create_dir=sftp_cfg.get("create_remote_dir", False),
                    logger=logger,
                )
                bitacora["resultado_sftp"] = "OK"
            except Exception as exc:
                logger.error("SFTP error: %s", exc)
                bitacora["resultado_sftp"] = "ERROR: %s" % exc
                bitacora["errores"].append(str(exc))
        else:
            logger.info("SFTP omitido.")

        save_state(state_file, friendly_name)

    except Exception as exc:
        logger.exception("Error crítico: %s", exc)
        bitacora["errores"].append(str(exc))

    finally:
        bitacora["tiempo_total_seg"] = round(time.time() - start_time, 2)
        logger.info(
            "FIN — sftp=%s | filas=%d | tiempo=%.2fs",
            bitacora["resultado_sftp"],
            bitacora["registros_filtrados"],
            bitacora["tiempo_total_seg"],
        )


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
