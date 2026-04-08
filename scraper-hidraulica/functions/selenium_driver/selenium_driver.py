"""
selenium_driver.py
------------------
Clase SeleniumDriver que encapsula la conexión a Chromium headless en AWS Lambda
y la lógica de scraping sobre la página hidráulica.

Responsabilidades:
  - connect()     → inicia el driver de Chromium headless
  - close()       → cierra el driver y libera recursos
  - go_to_url()   → navega a una URL y espera que el DOM cargue
  - scrape()      → itera por rango de fechas y por cada opción del <select>,
                    descarga el PDF correspondiente y lo sube a S3
"""

import io
import os
import logging
import tempfile
import time
from datetime import datetime, timedelta

import boto3
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Detectar si estamos corriendo en AWS Lambda
_IS_LAMBDA = os.environ.get("AWS_EXECUTION_ENV") is not None or os.path.exists("/var/task")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constantes de entorno  (defínelas en serverless.yml → environment)
# ---------------------------------------------------------------------------
SCRAPER_URL       = os.environ.get("SCRAPER_URL", "")
S3_BUCKET_NAME    = os.environ.get("S3_BUCKET_NAME", "")
S3_PREFIX         = os.environ.get("S3_PREFIX", "pdfs")

# Rutas de Chromium/ChromeDriver dentro de la Lambda Layer
CHROMIUM_PATH     = os.environ.get("CHROMIUM_PATH", "/opt/chrome/chrome")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/opt/chromedriver")

# Selectores de los elementos de la página
ANIO_SELECT_ID     = os.environ.get("SELECT_ELEMENT_ANIO_ID",    "ContentPlaceHolder1_DrpAnio")    # id del <select> de año
RECURSO_SELECT_ID  = os.environ.get("SELECT_ELEMENT_RECURSO_ID", "ContentPlaceHolder1_DrpRecurso") # id del <select> de recurso
DATE_FROM_ID       = os.environ.get("DATE_FROM_ID",       "txtFechaIni") # id del input fecha inicio
DATE_TO_ID         = os.environ.get("DATE_TO_ID",         "txtFechaFin") # id del input fecha fin
DOWNLOAD_BTN_ID    = os.environ.get("DOWNLOAD_BTN_ID",    "btnDescargar")# id del botón descargar/PDF
PDF_LINK_CSS       = os.environ.get("PDF_LINK_CSS",        "a.pdf-link") # selector del enlace al PDF generado

DATE_FORMAT        = "%d/%m/%Y"   # Formato de fecha que usa la página
PAGE_LOAD_TIMEOUT  = int(os.environ.get("PAGE_LOAD_TIMEOUT", "30"))      # segundos
ELEMENT_WAIT       = int(os.environ.get("ELEMENT_WAIT",      "15"))      # segundos


class SeleniumDriver:
    """
    Wrapper de Selenium headless preparado para ejecutarse en AWS Lambda.
    
    Uso típico:
        driver = SeleniumDriver()
        driver.connect()
        driver.go_to_url("https://ejemplo.com")
        results = driver.scrape(start_date, end_date)
        driver.close()
    """

    def __init__(self):
        self._driver: webdriver.Chrome | None = None
        self._s3_client = boto3.client("s3")

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    def connect(self) -> None:
        logger.info("SeleniumDriver.connect → iniciando driver (lambda=%s)", _IS_LAMBDA)

        options = Options()

        # Flags obligatorios para headless
        # Banderas clásicas para serverless-chrome v69
        options.add_argument("--headless") # En Chrome 60-100 es solo --headless
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--single-process") # OBLIGATORIO para serverless-chrome antiguo en AWS Lambda
        options.add_argument("--window-size=1280,800")
        options.add_argument("--user-data-dir=/tmp/chrome-user-data")

        # Directorio temporal para descargas automáticas
        self._download_dir = tempfile.mkdtemp()
        prefs = {
            "download.default_directory": self._download_dir,
            "download.prompt_for_download": False,
            "plugins.always_open_pdf_externally": True,
        }
        options.add_experimental_option("prefs", prefs)

        if _IS_LAMBDA:
            # ── Entorno Lambda: Usar serverless-chrome antiguo en /opt configurado en el Dockerfile
            options.binary_location = os.environ.get("CHROME_BIN", "/opt/headless-chromium")
            service = Service(executable_path=os.environ.get("CHROMEDRIVER_PATH", "/opt/chromedriver"))
        else:
            # ── Entorno local: usar Chrome del sistema + webdriver-manager
            try:
                from webdriver_manager.chrome import ChromeDriverManager
                from selenium.webdriver.chrome.service import Service as ChromeService
            except ImportError:
                raise ImportError(
                    "En local instala webdriver-manager: pip install webdriver-manager"
                )

            # macOS: Chrome en /Applications
            chrome_mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if os.path.exists(chrome_mac):
                options.binary_location = chrome_mac

            service = ChromeService(ChromeDriverManager().install())

        self._driver = webdriver.Chrome(service=service, options=options)
        self._driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        logger.info("SeleniumDriver.connect → driver listo ✓")

    # ------------------------------------------------------------------
    # Cierre de conexión
    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._driver:
            logger.info("SeleniumDriver.close → cerrando driver")
            try:
                self._driver.quit()
            except Exception as exc:
                logger.warning("SeleniumDriver.close → error al cerrar driver: %s", exc)
            finally:
                self._driver = None

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------
    def go_to_url(self, url: str) -> None:
        if not self._driver:
            raise RuntimeError("Driver no inicializado. Llama a connect() primero.")

        target = url or SCRAPER_URL
        self._driver.get(target)

        # Esperar a que el DOM esté completamente cargado
        WebDriverWait(self._driver, PAGE_LOAD_TIMEOUT).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        logger.info("SeleniumDriver.go_to_url → página cargada ✓")

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------
    def scrape(self, start_date: str, end_date: str) -> list[dict]:
        """
        Orquesta el scraping iterando por año (DrpAnio) y por cada recurso (DrpRecurso).

        Lógica:
          1. Compara el año de start_date y end_date.
          2. Si son el mismo año → itera solo ese año.
             Si son distintos  → itera todos los años en el rango [año_inicio, año_fin].
          3. Para cada año:
             a. Selecciona el año en DrpAnio (via JS).
             b. Lee todas las opciones de DrpRecurso (via JS).
             c. Selecciona cada recurso uno a uno (via JS), imprime el par y espera 2 s.

        Args:
            start_date: Fecha de inicio en formato DATE_FORMAT (ej. "01/04/2025").
            end_date:   Fecha de fin    en formato DATE_FORMAT (ej. "07/04/2025").

        Returns:
            Lista de dicts con el resultado por cada (año, recurso).
        """
        if not self._driver:
            raise RuntimeError("Driver no inicializado. Llama a connect() primero.")

        fecha_inicio = datetime.strptime(start_date, DATE_FORMAT)
        fecha_fin    = datetime.strptime(end_date,   DATE_FORMAT)

        if fecha_inicio > fecha_fin:
            raise ValueError(f"start_date ({start_date}) debe ser ≤ end_date ({end_date})")

        # ── 1. Determinar años a iterar ────────────────────────────────
        anio_inicio = fecha_inicio.year
        anio_fin    = fecha_fin.year

        if anio_inicio == anio_fin:
            logger.info(
                "scrape → mismo año (%d), no se iterará por múltiples años.", anio_inicio
            )
        else:
            logger.info(
                "scrape → rango de años detectado: %d → %d", anio_inicio, anio_fin
            )

        anios = self._get_years_to_iterate(anio_inicio, anio_fin)
        logger.info("scrape → años a procesar: %s", anios)

        results = []

        # ── 2. Bucle externo: años ─────────────────────────────────────
        for anio in anios:
            logger.info("══ Procesando año: %s ══", anio)

            # a. Seleccionar el año en DrpAnio via JS
            self._select_anio_js(str(anio))

            # b. Obtener todas las opciones de DrpRecurso via JS
            recursos = self._get_recurso_options_js()
            logger.info(
                "   → %d recursos disponibles para el año %s", len(recursos), anio
            )

            # c. Iterar cada recurso
            for recurso in recursos:
                valor_recurso = recurso["value"]
                texto_recurso = recurso["text"]

                # Seleccionar recurso via JS
                self._select_recurso_js(valor_recurso)

                # ── Imprimir el estado actual ───────────────────────────
                print(
                    f"[AÑO: {anio}]  [RECURSO: {texto_recurso!r} (value='{valor_recurso}')]  ✓ seleccionado"
                )

                # ── Esperar a que ASP.NET WebForms termine la petición ──
                # Sys.WebForms detecta cuando la petición asíncrona subyacente ha terminado de repintar la tabla.
                try:
                    from selenium.webdriver.support.ui import WebDriverWait
                    WebDriverWait(self._driver, 15).until(
                        lambda d: d.execute_script(
                            "return (typeof Sys === 'undefined' || !Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack());"
                        )
                    )
                except Exception:
                    logger.warning("Timeout esperando Sys.WebForms, intentando continuar de todas formas...")
                
                # Pequeña pausa extra para estabilizar el DOM
                time.sleep(1)

                # Descargar los PDFs presentes en la tabla para los días que apliquen a este año
                res_descarga = self._descargar_pdfs_tabla(
                    fecha_inicio=fecha_inicio,
                    fecha_fin=fecha_fin,
                    anio_actual=anio,
                    valor_recurso=valor_recurso,
                    texto_recurso=texto_recurso
                )
                results.extend(res_descarga)

        logger.info(
            "scrape → finalizado. Total registros: %d", len(results)
        )
        return results

    def scrape_cron(self) -> dict:
        """
        Ejecución programada automatizada (sin recibir --start/--end).
        1. Para cada opción de recurso (Presa).
        2. Toma únicamente la primerísima fila (el archivo MÁS RECIENTE).
        """
        # 1. Obtener todas las opciones de recurso en el DOM (del año predeterminado)
        opciones_recurso = self._driver.execute_script(f"""
            var sel = document.getElementById('{RECURSO_SELECT_ID}');
            if (!sel) return [];
            return Array.from(sel.options).map(o => ({{ value: o.value, text: o.text }}));
        """)
        
        results = []
        for opc in opciones_recurso:
            val_rec = opc['value']
            txt_rec = opc['text']
            
            # Select
            self._select_recurso_js(val_rec)
            
            try:
                from selenium.webdriver.support.ui import WebDriverWait
                WebDriverWait(self._driver, 15).until(
                    lambda d: d.execute_script(
                        "return (typeof Sys === 'undefined' || !Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack());"
                    )
                )
            except Exception:
                pass
            time.sleep(1)
            
            # Extraer solo la primera fila de la tabla
            filas_extraidas = self._driver.execute_script("""
                var panel = document.querySelector('#accordion #panel3');
                if (panel) {
                    var tbody = panel.querySelector('table tbody');
                    if (tbody) {
                        var trs = tbody.querySelectorAll('tr');
                        if (trs.length > 0) {
                            var tds = trs[0].querySelectorAll('td');
                            if (tds.length >= 3) {
                                var fechaTxt = tds[0].innerText.trim();
                                var aTag = tds[2].querySelector('a');
                                if (fechaTxt && aTag) {
                                    return [{ fecha_bd: fechaTxt, href: aTag.getAttribute('href') }];
                                }
                            }
                        }
                    }
                }
                return [];
            """)
            
            if not filas_extraidas:
                logger.warning("Modo cron: tabla vacia para recurso %s", txt_rec)
                continue
                
            fila = filas_extraidas[0]
            fecha_str = fila["fecha_bd"]
            url_pdf = fila["href"]
            
            estado_descarga = {
                "fecha": fecha_str,
                "recurso_valor": val_rec,
                "recurso_texto": txt_rec,
                "s3_key": None,
                "status": "error",
                "error": None
            }
            
            # Construir llaves asumiendo misma convención que descargas normales
            dia_str, mes_str, anio_str = fecha_str.split("/")
            opcion_s3 = val_rec.replace(" ", "_").replace("/", "-")
            s3_key = f"{S3_PREFIX}/{anio_str}/{mes_str}/{dia_str}/{opcion_s3}.pdf"
            
            # 2. Verificar si el archivo ya existe en S3
            ya_existe = False
            try:
                logger.debug("CRON verificando S3: bucket=%s, key=%s", S3_BUCKET_NAME, s3_key)
                self._s3_client.head_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
                ya_existe = True
            except Exception as e:
                # El error 404 significa que NO existe, lo cual está bien, procedemos.
                if "404" in str(e) or "Not Found" in str(e):
                    pass
                elif "locate credentials" in str(e) or "Could not connect" in str(e):
                    # Validar archivo en simulador / descargas_locales local PC
                    import os
                    fecha_local = f"{dia_str}-{mes_str}-{anio_str}"
                    local_path = os.path.join("descargas_locales", f"{opcion_s3}_{fecha_local}.pdf")
                    if os.path.exists(local_path):
                        ya_existe = True
            
            if ya_existe:
                print(f"CRON -> {txt_rec} -> ultimo archivo publicado ({fecha_str}) YA ESTABA GUARDADO")
                estado_descarga["status"] = "skipeado_ya_existe"
                estado_descarga["s3_key"] = s3_key
                results.append(estado_descarga)
                continue
                
            # 3. Descargarlo ya que no existe
            from urllib.parse import urljoin
            try:
                full_url = urljoin("https://www.cenace.gob.mx", url_pdf)
                resp = requests.get(full_url, timeout=30)
                resp.raise_for_status()
                
                s3_key_uploaded = self._subir_a_s3(resp.content, fecha_str, val_rec)
                estado_descarga["s3_key"] = s3_key_uploaded
                estado_descarga["status"] = "ok"
                print(f"CRON -> {txt_rec} -> ultimo archivo publicado ({fecha_str}) DESCARGADO NUEVO")
            except Exception as e:
                estado_descarga["error"] = str(e)
                print(f"CRON -> {txt_rec} -> error al descargar {fecha_str}: {e}")
                 
            results.append(estado_descarga)
        return results
    # ------------------------------------------------------------------
    # Helpers privados — Año / Recurso (via JavaScript)
    # ------------------------------------------------------------------

    def _get_years_to_iterate(self, anio_inicio: int, anio_fin: int) -> list[int]:
        """
        Devuelve la lista de años a iterar.

        Si inicio == fin  → [anio_inicio]
        Si son distintos  → range(anio_inicio, anio_fin + 1)

        Nota: se basa exclusivamente en los años de las fechas recibidas;
        no consulta el DOM del select de años para este paso.
        """
        return list(range(anio_inicio, anio_fin + 1))

    def _select_anio_js(self, valor: str) -> None:
        """
        Selecciona un año en #ContentPlaceHolder1_DrpAnio usando JavaScript.

        Usa JS directo para evitar problemas con Select de ASP.NET WebForms
        que a veces requieren eventos adicionales (change) para actualizar
        los selects dependientes.
        """
        wait = WebDriverWait(self._driver, ELEMENT_WAIT)
        wait.until(EC.presence_of_element_located((By.ID, ANIO_SELECT_ID)))

        self._driver.execute_script(
            """
            var sel = document.getElementById(arguments[0]);
            sel.value = arguments[1];
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            """,
            ANIO_SELECT_ID,
            valor,
        )
        logger.debug("_select_anio_js → año '%s' seleccionado", valor)

    def _get_recurso_options_js(self) -> list[dict]:
        """
        Obtiene todas las opciones de #ContentPlaceHolder1_DrpRecurso mediante JS.

        Returns:
            [{"value": "...", "text": "..."}, ...]  (excluye opciones sin value)
        """
        wait = WebDriverWait(self._driver, ELEMENT_WAIT)
        wait.until(EC.presence_of_element_located((By.ID, RECURSO_SELECT_ID)))

        opciones_raw: list[dict] = self._driver.execute_script(
            """
            var sel = document.getElementById(arguments[0]);
            var result = [];
            for (var i = 0; i < sel.options.length; i++) {
                var opt = sel.options[i];
                if (opt.value) {
                    result.push({ value: opt.value, text: opt.text.trim() });
                }
            }
            return result;
            """,
            RECURSO_SELECT_ID,
        )
        logger.debug(
            "_get_recurso_options_js → %d opciones obtenidas", len(opciones_raw)
        )
        return opciones_raw

    def _select_recurso_js(self, valor: str) -> None:
        """
        Selecciona un recurso en #ContentPlaceHolder1_DrpRecurso usando JavaScript.

        Dispara el evento 'change' con bubbles=true para que ASP.NET
        UpdatePanel y ViewState reaccionen correctamente.
        """
        self._driver.execute_script(
            """
            var sel = document.getElementById(arguments[0]);
            sel.value = arguments[1];
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            """,
            RECURSO_SELECT_ID,
            valor,
        )
        logger.debug("_select_recurso_js → recurso '%s' seleccionado", valor)


    def _descargar_pdfs_tabla(
        self, fecha_inicio: datetime, fecha_fin: datetime, anio_actual: int, valor_recurso: str, texto_recurso: str
    ) -> list[dict]:
        """
        Extrae los links de la tabla de PDFs y descarga los que correspondan al rango de fechas 
        para el año actual.
        """
        from urllib.parse import urljoin
        
        resultados = []
        dias_a_buscar = []
        
        # Determinar qué días buscar (los del rango que caen en este anio_actual)
        fecha_iter = fecha_inicio
        while fecha_iter <= fecha_fin:
            if fecha_iter.year == anio_actual:
                dias_a_buscar.append(fecha_iter)
            fecha_iter += timedelta(days=1)
            
        if not dias_a_buscar:
            return []
            
        # Extraer filas estructuradas de la tabla en el DOM
        filas_extraidas = self._driver.execute_script("""
            var results = [];
            var panel = document.querySelector('#accordion #panel3');
            if (panel) {
                var tbody = panel.querySelector('table tbody');
                if (tbody) {
                    var trs = tbody.querySelectorAll('tr');
                    for (var i = 0; i < trs.length; i++) {
                        var tds = trs[i].querySelectorAll('td');
                        if (tds.length >= 3) {
                            var fechaTxt = tds[0].innerText.trim();
                            var aTag = tds[2].querySelector('a');
                            if (fechaTxt && aTag) {
                                results.push({
                                    fecha_bd: fechaTxt, 
                                    href: aTag.getAttribute('href')
                                });
                            }
                        }
                    }
                }
            }
            return results;
        """)
        
        if not filas_extraidas:
            logger.warning("No se encontraron filas con PDF en la tabla para el año %s recurso '%s'", anio_actual, texto_recurso)
            return resultados
            
        base_url = "https://www.cenace.gob.mx"
        
        for dia in dias_a_buscar:
            fecha_str = dia.strftime(DATE_FORMAT)  # ej: 01/01/2023
            patron_fecha = dia.strftime("%Y%m%d") + ".pdf"  # ej: 20230101.pdf
            
            # Buscar en las filas extraidas una que coincida con la fecha str
            url_pdf = None
            for fila in filas_extraidas:
                if fila["fecha_bd"] == fecha_str:
                    # Sobre esa misma fila, validar el href que termina en el patrón deseado
                    if patron_fecha in fila["href"]:
                        url_pdf = fila["href"]
                        break
                    
            if not url_pdf:
                resultados.append({
                    "fecha": fecha_str,
                    "anio": str(anio_actual),
                    "recurso_valor": valor_recurso,
                    "recurso_texto": texto_recurso,
                    "s3_key": None,
                    "status": "not_found",
                    "error": "El PDF no está disponible en la tabla para esta fecha"
                })
                print(f"{anio_actual} -> {texto_recurso} -> archivo No se encontro para {dia.strftime('%d-%m-%Y')}")
                continue
                
            estado_descarga = {
                "fecha": fecha_str,
                "anio": str(anio_actual),
                "recurso_valor": valor_recurso,
                "recurso_texto": texto_recurso,
                "s3_key": None,
                "status": "error",
                "error": None
            }
            
            try:
                full_url = urljoin(base_url, url_pdf)
                logger.debug("Descargando PDF desde la tabla: %s", full_url)
                resp = requests.get(full_url, timeout=30)
                resp.raise_for_status()
                
                s3_key = self._subir_a_s3(
                    pdf_bytes=resp.content,
                    fecha_str=fecha_str,
                    opcion=valor_recurso
                )
                
                estado_descarga["s3_key"] = s3_key
                estado_descarga["status"] = "ok"
                print(f"{anio_actual} -> {texto_recurso} -> archivo descargado para {dia.strftime('%d-%m-%Y')}")
            except Exception as e:
                estado_descarga["error"] = str(e)
                print(f"{anio_actual} -> {texto_recurso} -> archivo marco error para {dia.strftime('%d-%m-%Y')}: {e}")
                
            resultados.append(estado_descarga)
            
        return resultados

    def _subir_a_s3(self, pdf_bytes: bytes, fecha_str: str, opcion: str) -> str:
        """
        Sube el PDF a S3 y devuelve la clave (key) resultante.

        Estructura: {S3_PREFIX}/{anio}/{mes}/{dia}/{opcion}.pdf
          Ej: pdfs/2025/04/07/RecursoA.pdf
        """
        dia_str, mes_str, anio_str = fecha_str.split("/")
        opcion_s3 = opcion.replace(" ", "_").replace("/", "-")
        s3_key = f"{S3_PREFIX}/{anio_str}/{mes_str}/{dia_str}/{opcion_s3}.pdf"

        try:
            self._s3_client.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                Body=pdf_bytes,
                ContentType="application/pdf",
            )
            return s3_key
        except Exception as exc:
            # Si corre en local sin AWS configurado, guardamos físicamente para que el usuario lo vea
            if "locate credentials" in str(exc) or "Could not connect" in str(exc):
                os.makedirs("descargas_locales", exist_ok=True)
                fecha_local = f"{dia_str}-{mes_str}-{anio_str}"
                local_path = os.path.join("descargas_locales", f"{opcion_s3}_{fecha_local}.pdf")
                with open(local_path, "wb") as f:
                    f.write(pdf_bytes)
                logger.info("   ⚠️ Aviso: Sin AWS Keys. Archivo guardado localmente en: %s", local_path)
                return f"local://{local_path}"
            
            raise exc
