FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.11

# ── Dependencias del sistema necesarias para Chrome headless ─────────────────
RUN yum install -y \
    unzip \
    wget \
    curl \
    ca-certificates \
    atk \
    cups-libs \
    gtk3 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    alsa-lib \
    libX11 \
    libX11-xcb \
    libxcb \
    xorg-x11-server-Xvfb \
    && yum clean all

# ── Chrome y ChromeDriver (Chrome for Testing — versiones sincronizadas) ──────
# NOTA: Se usa Chrome for Testing en vez del RPM de Google Chrome porque la
# API antigua (chromedriver.storage.googleapis.com) fue deprecada en 2024.
# Ambos binarios provienen de la misma fuente → versión garantizada compatible.
ARG CHROME_VERSION=131.0.6778.204

RUN wget -q -O /tmp/chrome-linux64.zip \
        "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip" \
    && unzip -q /tmp/chrome-linux64.zip -d /opt/ \
    && ln -sf /opt/chrome-linux64/chrome /usr/bin/google-chrome \
    && chmod +x /opt/chrome-linux64/chrome \
    && rm /tmp/chrome-linux64.zip

RUN wget -q -O /tmp/chromedriver-linux64.zip \
        "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip" \
    && unzip -q /tmp/chromedriver-linux64.zip -d /tmp/ \
    && mv /tmp/chromedriver-linux64/chromedriver /usr/bin/chromedriver \
    && chmod +x /usr/bin/chromedriver \
    && rm -rf /tmp/chromedriver-linux64.zip /tmp/chromedriver-linux64

# ── Dependencias Python ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ── Código de la aplicación ───────────────────────────────────────────────────
COPY scraper_handler.py ${LAMBDA_TASK_ROOT}
COPY selenium_driver/ ${LAMBDA_TASK_ROOT}/selenium_driver/

# ── Handler ───────────────────────────────────────────────────────────────────
CMD ["scraper_handler.run_scraper"]