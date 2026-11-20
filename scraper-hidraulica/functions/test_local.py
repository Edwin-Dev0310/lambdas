#!/usr/bin/env python3
"""
test_local.py
-------------
Script para probar el scraper localmente sin necesidad de deployar a AWS Lambda.

Uso:
    python test_local.py --start 01/04/2025 --end 03/04/2025

Qué hace diferente al entorno Lambda:
  - Usa webdriver-manager para descargar ChromeDriver automáticamente.
  - Usa el Chrome de /Applications en macOS.
  - Usa credenciales de AWS de tu máquina (~/.aws/credentials).
  - Mucho más rápido para iterar que desplegar.
"""

import argparse
import json
import logging
import os
import sys

# ── Agregar el directorio /functions al path para que los imports funcionen ──
sys.path.insert(0, os.path.dirname(__file__))

# ── Logging legible en consola ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s → %(message)s",
    datefmt="%H:%M:%S",
)

# ── Variables de entorno locales (sobreescribe antes de importar el handler) ──
# Ajusta S3_BUCKET_NAME al bucket real donde quieres guardar los PDFs de prueba.
os.environ.setdefault("S3_BUCKET_NAME",    "sim-market-data")
os.environ.setdefault("S3_PREFIX",         "evolucion-hidraulica-presas/test")
os.environ.setdefault("SELECT_ELEMENT_ANIO_ID", "ContentPlaceHolder1_DrpAnio")
os.environ.setdefault("SELECT_ELEMENT_RECURSO_ID", "ContentPlaceHolder1_drpPresas")
os.environ.setdefault("PAGE_LOAD_TIMEOUT", "30")
os.environ.setdefault("ELEMENT_WAIT",      "15")

# ── Importar el handler principal ─────────────────────────────────────────────
from scraper_handler import run_scraper


def main():
    parser = argparse.ArgumentParser(
        description="Prueba local del scraper hidráulico CENACE"
    )
    parser.add_argument(
        "--start",
        required=False,
        metavar="DD/MM/YYYY",
        help="Fecha de inicio del rango a scrapear (ej. 01/04/2025)",
    )
    parser.add_argument(
        "--end",
        required=False,
        metavar="DD/MM/YYYY",
        help="Fecha de fin del rango a scrapear (ej. 03/04/2025)",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Simular evento diario (sin pasar fechas)",
    )
    args = parser.parse_args()

    if args.cron:
        # Simulamos cron mandando dict vacío
        start_date = ""
        end_date = ""
    else:
        # Fallback dinámico al día de hoy si corren desde IDE sin argumentos
        import datetime
        hoy_str = datetime.datetime.now().strftime("%d/%m/%Y")
        start_date = args.start or hoy_str
        end_date = args.end or hoy_str

    # Simular el evento que llegaría a la Lambda
    event = {
        "start_date": start_date,
        "end_date":   end_date,
    }

    if getattr(args, "dry_run", False):
        _dry_run(start_date)
        return

    print(f"\n🚀 Iniciando scraper local: {start_date} → {end_date}\n")
    response = run_scraper(event, context=None)

    print("\n" + "=" * 60)
    print("RESULTADO:")
    print("=" * 60)
    body = json.loads(response["body"])
    print(json.dumps(body, indent=2, ensure_ascii=False, default=str))

    exitosos = body.get("exitosos", 0)
    fallidos  = body.get("fallidos", 0)
    total     = body.get("total", 0)
    print(f"\n✅ Exitosos: {exitosos} / {total}   ❌ Fallidos: {fallidos} / {total}")


def _dry_run(start_date: str):
    """
    Modo de prueba rápida: abre la página y lista las opciones de DrpRecurso via JS.
    Si el elemento no se encuentra, vuelca todos los <select> disponibles para diagnóstico.
    """
    from selenium_driver.selenium_driver import SeleniumDriver, RECURSO_SELECT_ID, ANIO_SELECT_ID
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    url = "https://www.cenace.gob.mx/Paginas/Info/EvolucionHidraulica.aspx"
    print(f"\n🔍 DRY RUN — abriendo: {url}")
    print(f"   Buscando select con id='{RECURSO_SELECT_ID}'\n")

    driver = SeleniumDriver()
    try:
        driver.connect()
        driver.go_to_url(url)

        d = driver._driver

        # ── Volcar iframes detectados (ASP.NET a veces los usa) ──────────
        iframes = d.execute_script(
            "return Array.from(document.querySelectorAll('iframe')).map(f => f.id || f.name || f.src);"
        )
        if iframes:
            print(f"⚠️  Se encontraron {len(iframes)} iframe(s) en la página:")
            for f in iframes:
                print(f"     → {f}")
            print()

        # ── Volcar TODOS los <select> presentes en el DOM ─────────────────
        all_selects = d.execute_script(
            """
            return Array.from(document.querySelectorAll('select')).map(function(s) {
                return { id: s.id, name: s.name, options: s.options.length };
            });
            """
        )
        print(f"📋 Selects encontrados en el DOM ({len(all_selects)} total):")
        for s in all_selects:
            marker = " ← ✓ OBJETIVO" if s["id"] in (RECURSO_SELECT_ID, ANIO_SELECT_ID) else ""
            print(f"     id='{s['id']}'  name='{s['name']}'  opciones={s['options']}{marker}")
        print()

        # ── Intentar localizar el select objetivo ─────────────────────────
        try:
            wait = WebDriverWait(d, 10)
            wait.until(EC.presence_of_element_located((By.ID, RECURSO_SELECT_ID)))
        except TimeoutException:
            print(f"❌ No se encontró el elemento id='{RECURSO_SELECT_ID}' en 10 s.")
            print("   Revisa los IDs listados arriba y actualiza RECURSO_SELECT_ID.")
            return

        opciones = d.execute_script(
            """
            var sel = document.getElementById(arguments[0]);
            var result = [];
            for (var i = 0; i < sel.options.length; i++) {
                result.push({ value: sel.options[i].value, text: sel.options[i].text.trim() });
            }
            return result;
            """,
            RECURSO_SELECT_ID,
        )
        print(f"✅ Select '{RECURSO_SELECT_ID}' encontrado con {len(opciones)} opciones:\n")
        for i, opt in enumerate(opciones):
            valor = opt["value"]
            texto = opt["text"]
            if valor:
                print(f"  [{i:02d}] value='{valor}'  →  '{texto}'")

    finally:
        driver.close()

if __name__ == "__main__":
    main()
