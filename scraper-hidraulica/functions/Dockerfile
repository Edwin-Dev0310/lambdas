FROM public.ecr.aws/lambda/python:3.11

# Instalar dependencias del sistema necesarias para Chromium
RUN yum install -y \
    unzip \
    wget \
    curl \
    atk \
    cups-libs \
    gtk3 \
    libXcomposite \
    alsa-lib \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    xorg-x11-fonts-Type1 \
    xorg-x11-fonts-misc \
    libX11 \
    nss \
    mesa-libgbm \
    && yum clean all

# Instalar Chromium
RUN curl -Lo /tmp/chromium.zip https://github.com/adieuadieu/serverless-chrome/releases/download/v1.0.0-55/stable-headless-chromium-amazonlinux-2017-03.zip \
    && unzip /tmp/chromium.zip -d /opt/ \
    && rm /tmp/chromium.zip \
    && chmod +x /opt/headless-chromium

# Instalar ChromeDriver compatible
RUN curl -Lo /tmp/chromedriver.zip https://chromedriver.storage.googleapis.com/2.46/chromedriver_linux64.zip \
    && unzip /tmp/chromedriver.zip -d /opt/ \
    && rm /tmp/chromedriver.zip \
    && chmod +x /opt/chromedriver

# Variables de entorno
ENV CHROME_BIN=/opt/headless-chromium
ENV CHROMEDRIVER_PATH=/opt/chromedriver

# Copiar dependencias Python
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar todos los archivos incluyendo subdirectorios al task root
COPY . ${LAMBDA_TASK_ROOT}

# Comando Lambda
CMD ["scraper_handler.run_scraper"]
