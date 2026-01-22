from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime, timedelta

import time
import os
import base64
import requests

# ==========================
# CONFIGURACIÓN
# ==========================
URL = "http://192.168.88.245/"
PASSWORD = "letmein"

EMAIL_ENDPOINT = "https://5fkeolmyi6.execute-api.us-east-1.amazonaws.com/minador-send-email"
TO_ADDRESS = ['mflores@ammper.com']
CC_ADDRESS = ['imoran@ammper.com']
BCC_ADDRESS =['mflores@ammper.com']
SUBJECT = "Captura minador"
BODY = (
        "Hola,\n\n"
        "Adjunto evidencia de captura de pantalla del minador.\n\n"
        "Saludos."
    )

# ==========================
# SCREENSHOT CON FECHA
# ==========================
timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M")
os.makedirs("screenshots", exist_ok=True)
SCREENSHOT_FILE = f"screenshots/captura_{timestamp}.png"
SCREENSHOT_NAME = f"captura_{timestamp}.png"

# ==========================
# CHROME (HEADLESS – CRON FRIENDLY)
# ==========================
options = Options()
options.add_argument("--window-size=1920,1080")
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

wait = WebDriverWait(driver, 20)
actions = ActionChains(driver)

def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def send_email(image_b64, image_name):
    payload = {
        "to_address": TO_ADDRESS,
        "cc": CC_ADDRESS,
        "bcc": BCC_ADDRESS,
        "subject": SUBJECT,
        "body": BODY,
        "image_data": image_b64,
        "image_name": image_name
    }

    try:
        response = requests.post(
            EMAIL_ENDPOINT,
            json=payload,
            timeout=30
        )
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        raise

def cleanup_old_screenshots(folder="screenshots", days=30):
    now = datetime.now()
    cutoff = now - timedelta(days=days)
    print(f"⚠️ borrando")

    if not os.path.exists(folder):
        return

    for filename in os.listdir(folder):
        if not filename.lower().endswith(".png"):
            continue

        filepath = os.path.join(folder, filename)

        try:
            # Fecha de modificación del archivo
            file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
        except Exception:
            continue

        if file_time < cutoff:
            try:
                os.remove(filepath)
                print(f"🗑️ Eliminado screenshot antiguo: {filename}")
            except Exception as e:
                print(f"⚠️ No se pudo borrar {filename}: {e}")

try:
    # ==========================
    # ABRIR URL
    # ==========================
    driver.get(URL)
    time.sleep(4)
    # ==========================
    # PASSWORD (JS - React/MUI)
    # ==========================
    password_input = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//input[@type='password']")
        )
    )

    driver.execute_script("""
        const input = arguments[0];
        const value = arguments[1];

        // 1. Focus real
        input.focus();

        // 2. Setter nativo (CRÍTICO PARA REACT)
        const nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            'value'
        ).set;

        nativeSetter.call(input, value);

        // 3. Eventos que React escucha
        input.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            inputType: 'insertText',
            data: value
        }));

        input.dispatchEvent(new Event('change', { bubbles: true }));

        // 4. Blur final
        input.blur();
    """, password_input, PASSWORD)

    time.sleep(4)

    # ==========================
    # BOTÓN "Enter Password"
    # ==========================
    login_button = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[normalize-space()='Enter Password']")
        )
    )

    actions.move_to_element(login_button).pause(0.2).click().perform()

    # ==========================
    # ESPERA POST Input
    # ==========================
    time.sleep(5)

    # ==========================
    # FULL PAGE SCREENSHOT
    # ==========================
    width = driver.execute_script("return document.body.scrollWidth")
    height = driver.execute_script("return document.body.scrollHeight")
    driver.set_window_size(width, height)
    time.sleep(2)

    driver.save_screenshot(SCREENSHOT_FILE)
    print(f"✅ Screenshot FULL PAGE guardado: {SCREENSHOT_FILE}")

    # ==========================
    # BASE64 + ENVÍO EMAIL
    # ==========================
    image_b64 = image_to_base64(SCREENSHOT_FILE)
    send_email(image_b64, SCREENSHOT_NAME)
    print("enviado por correo correctamente")

    # ==========================
    # LIMPIEZA DE SCREENSHOTS (>30 días)
    # ==========================
    cleanup_old_screenshots(folder="screenshots", days=30)
finally:
    if driver:
        print("🧹 Cerrando navegador...\n\n\n")
        try:
            driver.close()
        except:
            pass
        try:
            driver.quit()
        except:
            pass