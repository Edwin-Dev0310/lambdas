"""
scraper_handler.py
------------------
Handler principal de la Lambda de scraping hidráulico.

Recibe un evento con:
  {
    "start_date": "01/04/2025",   # formato DD/MM/YYYY
    "end_date":   "07/04/2025"    # formato DD/MM/YYYY
  }
Opcionalmente, si se ejecuta sin fechas (ej. evento Cron programado de EventBridge),
detectará "MODO CRON" y descargará directamente los últimos PDFs publicados.

Flujo:
  1. Valida los parámetros de entrada.
  2. Instancia SeleniumDriver y abre la URL.
  3. Llama a scrape(start_date, end_date) o scrape_cron()
  4. Sube PDFs a S3.
  5. Retorna un resumen JSON.
"""

import json
import logging
import traceback
from datetime import datetime

from selenium_driver.selenium_driver import SeleniumDriver

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DATE_FORMAT = "%d/%m/%Y"
SCRAPER_URL = "https://www.cenace.gob.mx/Paginas/Info/EvolucionHidraulica.aspx"


def run_scraper(event: dict, context) -> dict:
    """
    Entry-point de la Lambda / API Gateway / Cron.
    """
    logger.info("Recibido evento: %s", json.dumps(event))

    # Support HTTP body string from API Gateway
    if "body" in event:
        payload = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
    else:
        payload = event

    start_date = payload.get("start_date", "").strip()
    end_date   = payload.get("end_date", "").strip()

    # ────────────────────────────────────────────────────────
    # MODO CRON (Sin parámetros de fecha)
    # ────────────────────────────────────────────────────────
    if not start_date and not end_date:
        logger.info("run_scraper → MODO EVENTO DIARIO (CRON). No se pasaron fechas.")
        driver = SeleniumDriver()
        try:
            driver.connect()
            driver.go_to_url(SCRAPER_URL)
            results = driver.scrape_cron()
            total = len(results)
            exitosos = sum(1 for r in results if r.get("status") in ("ok", "skipeado_ya_existe"))
            return _response(200, {
                "start_date": "cron",
                "end_date": "cron",
                "total": total,
                "exitosos": exitosos,
                "fallidos": total - exitosos,
                "detalle": results,
            })
        except Exception as exc:
            logger.exception("Error crítico durante scrape_cron")
            return _response(500, {"error": str(exc), "trace": traceback.format_exc()})
        finally:
            driver.close()

    # ────────────────────────────────────────────────────────
    # MODO TRADICIONAL (Con fechas)
    # ────────────────────────────────────────────────────────
    try:
        start_clean = start_date.replace("-", "/")
        end_clean   = end_date.replace("-", "/")
        fecha_inicio = datetime.strptime(start_clean, DATE_FORMAT)
        fecha_fin    = datetime.strptime(end_clean,   DATE_FORMAT)
        if fecha_inicio > fecha_fin:
            raise ValueError(f"start_date ({start_date}) no puede ser mayor que end_date ({end_date}).")
    except ValueError as exc:
        logger.error("Error parseo fechas: %s", exc)
        return _response(400, {"error": f"Formato de fecha inválido. Se esperaba {DATE_FORMAT}. Detalles: {exc}"})

    logger.info("run_scraper → start_date=%s  end_date=%s", start_date, end_date)

    driver = SeleniumDriver()
    try:
        driver.connect()
        driver.go_to_url(SCRAPER_URL)
        results = driver.scrape(start_date=start_date, end_date=end_date)
    except Exception as exc:
        logger.exception("Error crítico durante el scraping.")
        return _response(500, {"error": f"Error interno: {str(exc)}"})
    finally:
        driver.close()

    total    = len(results)
    exitosos = sum(1 for r in results if r.get("status") in ("ok", "skipeado_ya_existe"))

    summary = {
        "start_date":  start_date,
        "end_date":    end_date,
        "total":       total,
        "exitosos":    exitosos,
        "fallidos":    total - exitosos,
        "detalle":     results,
    }

    logger.info("run_scraper → completado. exitosos=%d total=%d", exitosos, total)
    return _response(200, summary)


def _response(status_code: int, body: dict) -> dict:
    """Construye una respuesta HTTP-like estándar para Lambda / API Gateway."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
