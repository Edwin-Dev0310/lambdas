# S3-Extractor-Cron

Versión simplificada de un solo archivo (`s3_extractor.py`) pensada para
ejecutarse periódicamente vía **crontab**. No tiene loop propio: cada
ejecución hace una sola pasada (listar → descargar → verificar → mover) y
termina. La periodicidad la controla el cron.

## Instalación

```bash
cd S3-Extractor-Cron
python3.12 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Editar .env con sus valores (bucket, prefix, region, etc.)
```

## Ejecución manual

```bash
python s3_extractor.py
```

## Programar en crontab (Linux)

```bash
crontab -e
```

Agregar, por ejemplo, para ejecutar cada 5 minutos:

```
*/5 * * * * /ruta/a/S3-Extractor-Cron/venv/bin/python /ruta/a/S3-Extractor-Cron/s3_extractor.py >> /ruta/a/S3-Extractor-Cron/logs/cron.log 2>&1
```

## Comportamiento ante errores

Cada operación S3 (listar/descargar/mover) reintenta automáticamente hasta
`MAX_RETRIES` veces (por defecto 3, con backoff exponencial) solo ante
errores transitorios de AWS (timeouts, throttling, 5xx). Si un archivo
falla definitivamente, queda intacto en el prefijo de entrada de S3 y se
reintenta en la siguiente ejecución del cron. Todo se registra en
`LOG_FILE` con fecha, hora, archivo y estado.
