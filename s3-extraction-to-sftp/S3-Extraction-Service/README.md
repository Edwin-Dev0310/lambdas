# S3 Extraction Service

Aplicación de consola en Python 3.12 que extrae archivos pendientes de un
bucket S3 (a través de un API Gateway + Lambda) y los entrega a un servidor
**SFTP**, sin usar credenciales de AWS ni acceder al bucket directamente.

Está diseñada para ser ejecutada por un **CRON** (Linux) o el **Task
Scheduler** de Windows, por ejemplo cada 5 minutos. **No implementa polling,
servicios de Windows, demonios, timers, hilos ni ningún proceso residente**:
cada invocación hace una sola pasada y termina.

## Arquitectura

```
Cron / Task Scheduler
        |
        v
S3 Extraction Service
        |
        v
      HTTPS
        |
        v
   API Gateway
        |
        v
   AWS Lambda  (s3-extraction-gateway-lambda)
        |
        v
    Amazon S3
```

El servicio nunca habla con S3 ni con AWS directamente: solo con el API
Gateway (autenticado con `x-api-key`) y con el servidor SFTP destino
(autenticado con usuario/contraseña). Las descargas de contenido usan URLs
prefirmadas de S3 generadas por la Lambda, válidas por tiempo limitado.

## Flujo de cada ejecución

1. Consultar `POST /files` con `action: pending` — obtiene los archivos
   pendientes bajo `SOURCE_PREFIX`, cada uno con su `downloadUrl` ya incluido.
2. Para cada archivo:
   1. Crear la estructura de carpetas local a partir de `relativePath`.
   2. Descargar el archivo desde `downloadUrl`.
   3. Validar que el tamaño descargado coincida con el informado por el API.
   4. Subir el archivo por SFTP, creando automáticamente la misma
      estructura de carpetas en `SFTP_REMOTE_PATH`.
   5. **Confirmar con el propio servidor SFTP** que el archivo quedó
      escrito: tras subir y renombrar, se hace un `stat()` remoto y se
      valida que el tamaño coincida con el informado por el API. Se
      registra en el log el resultado exacto de cada paso (bytes escritos
      por `put()`, y tamaño confirmado por `stat()`), no solo "se subió sin
      error".
   6. Confirmar el procesamiento con `action: processed`.
   7. Según `KEEP_LOCAL_FILES` (ver Configuración): mover el archivo local a
      `DOWNLOAD_PATH/processed/<relativePath>` (por defecto, `true`) o
      eliminarlo (`false`).
   8. Si algo falla en cualquiera de los pasos anteriores: el archivo local
      no se mueve a `processed` ni se elimina (se descarta solo si la
      descarga quedó corrupta), se registra el error (con stacktrace) y se
      continúa con el siguiente archivo. El archivo queda pendiente para el
      próximo ciclo del cron.
3. Finalizar.

> **Diagnóstico si el archivo no aparece en el SFTP:** revisa el log en
> busca de la línea `SFTP confirmó el archivo en destino: '<ruta>' (N
> bytes)`. Si esa línea aparece, el archivo sí quedó escrito en esa ruta
> exacta — si no lo ves ahí donde miras, es casi siempre porque el usuario
> SFTP tiene un *chroot* (carpeta raíz distinta a la que ves navegando por
> otro medio) y esa ruta absoluta apunta a otro lugar del que esperas. Si en
> cambio ves un `SftpError` (`no aparece en el servidor SFTP` o `tamaño
> remoto ... no coincide`), el archivo realmente no se subió bien y el
> archivo queda pendiente para el siguiente ciclo.

> **Nota de diseño:** la conexión SFTP se abre una sola vez por ejecución y
> se reutiliza para todo el lote (en vez de reconectar por cada archivo),
> por eficiencia. El flujo funcional (conectar, crear estructura remota,
> subir, verificar) se cumple igual para cada archivo.

## Estructura del proyecto

```
S3-Extraction-Service/
├── app/
│   ├── config.py            # Carga y validacion de variables de entorno (.env)
│   ├── api_client.py        # Cliente del API Gateway: acciones pending/processed
│   ├── download_manager.py  # Descarga por HTTPS + verificacion de tamaño
│   ├── sftp_client.py       # Conexion SFTP, creacion de carpetas y subida
│   ├── logger.py            # Logging con rotacion automatica
│   ├── service.py           # Orquestacion del ciclo completo (una pasada)
│   └── main.py               # Punto de entrada de consola
├── requirements.txt
├── .env.example
└── README.md
```

## Configuración

Copie `.env.example` a `.env` (en la raíz del proyecto) y complete los
valores:

| Variable | Descripción |
|---|---|
| `API_URL` | URL base del API Gateway (sin `/files` al final). |
| `API_KEY` | API Key para el header `x-api-key`. |
| `SOURCE_PREFIX` | Prefijo S3 de archivos pendientes; debe coincidir con el `SOURCE_PREFIX` configurado en la Lambda. |
| `DOWNLOAD_PATH` | Carpeta local raíz donde se descargan los archivos. |
| `KEEP_LOCAL_FILES` | `true` (por defecto): al confirmar el procesado, mueve el archivo a `DOWNLOAD_PATH/processed/<relativePath>` en vez de eliminarlo. `false`: lo elimina. |
| `STRIP_RELATIVE_PREFIX` | Sub-ruta inicial de `relativePath` a quitar antes de construir la ruta local y la ruta SFTP (ver ejemplo abajo). Vacío por defecto: no recorta nada. |
| `SFTP_HOST` / `SFTP_PORT` | Host y puerto del servidor SFTP destino. |
| `SFTP_USER` / `SFTP_PASSWORD` | Credenciales del servidor SFTP. |
| `SFTP_REMOTE_PATH` | Carpeta remota raíz (ruta absoluta) en el SFTP. |
| `LOG_LEVEL` | Nivel de logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_FILE` | Ruta del archivo de log (por defecto `logs/service.log`). |
| `MAX_RETRIES` | Reintentos máximos ante errores transitorios (por defecto 3). |
| `RETRY_WAIT_SECONDS` | Espera fija entre reintentos, en segundos (por defecto 5). |
| `REQUEST_TIMEOUT_SECONDS` | Timeout de red para API, descargas y SFTP (por defecto 30). |

### Ejemplo de `STRIP_RELATIVE_PREFIX`

Si el API devuelve `relativePath = "carga/2026/07/15/xml/factura001.xml"` pero
`carga/` es solo una carpeta interna de subida que no debe existir ni en tu
carpeta local ni en el SFTP, configura:

```
STRIP_RELATIVE_PREFIX=carga
```

Con eso, tanto la descarga local como la subida por SFTP (y el movido a
`processed/`) usan `2026/07/15/xml/factura001.xml` en vez de la ruta
completa. Si un archivo no tiene ese prefijo, se usa su `relativePath`
sin cambios (no se rompe nada).

## Instalación

### Linux

```bash
cd S3-Extraction-Service
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Editar .env con sus valores
```

### Windows

```powershell
cd S3-Extraction-Service
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# Editar .env con sus valores
```

## Ejecución manual

```bash
# Linux
venv/bin/python app/main.py

# Windows
venv\Scripts\python.exe app\main.py
```

## Programar cada 5 minutos

### CRON (Linux)

```bash
crontab -e
```

```
*/5 * * * * /ruta/a/S3-Extraction-Service/venv/bin/python /ruta/a/S3-Extraction-Service/app/main.py >> /ruta/a/S3-Extraction-Service/logs/cron.log 2>&1
```

### Task Scheduler (Windows)

Crear una tarea con:

- **Programa/script**: `C:\ruta\a\S3-Extraction-Service\venv\Scripts\python.exe`
- **Argumentos**: `C:\ruta\a\S3-Extraction-Service\app\main.py`
- **Desencadenador**: repetir la tarea cada 5 minutos.
- **Condiciones**: "Ejecutar tanto si el usuario inició sesión como si no".

O bien desde PowerShell (como Administrador):

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\ruta\a\S3-Extraction-Service\venv\Scripts\python.exe" -Argument "C:\ruta\a\S3-Extraction-Service\app\main.py"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "S3ExtractionService" -Action $action -Trigger $trigger -RunLevel Highest
```

## Ejemplo de ejecución

```
$ python app/main.py
2026-07-15 10:20:01 | INFO     | s3_extraction_service | Inicio de ejecución de S3 Extraction Service.
2026-07-15 10:20:02 | INFO     | s3_extraction_service | Archivos pendientes encontrados: 2 (requestId=8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3).
2026-07-15 10:20:02 | INFO     | s3_extraction_service | Conexión SFTP establecida con 192.168.1.20:22.
2026-07-15 10:20:03 | INFO     | s3_extraction_service | Descargado: 'fractar/unprocessed/2026/07/15/xml/factura001.xml' -> 'C:\Temp\Downloads\2026\07\15\xml\factura001.xml'.
2026-07-15 10:20:03 | INFO     | s3_extraction_service | SFTP put() OK: '/entrada/2026/07/15/xml/factura001.xml.part' (25478 bytes escritos en el temporal).
2026-07-15 10:20:03 | INFO     | s3_extraction_service | SFTP confirmó el archivo en destino: '/entrada/2026/07/15/xml/factura001.xml' (25478 bytes).
2026-07-15 10:20:03 | INFO     | s3_extraction_service | Subido y confirmado por SFTP: 'fractar/unprocessed/2026/07/15/xml/factura001.xml' -> '/entrada/2026/07/15/xml/factura001.xml'.
2026-07-15 10:20:04 | INFO     | s3_extraction_service | Procesamiento confirmado: 'fractar/unprocessed/2026/07/15/xml/factura001.xml'.
2026-07-15 10:20:04 | INFO     | s3_extraction_service | Movido a procesados localmente: 'C:\Temp\Downloads\processed\2026\07\15\xml\factura001.xml'.
2026-07-15 10:20:04 | INFO     | s3_extraction_service | Descargado: 'fractar/unprocessed/2026/07/15/xml/factura002.xml' -> 'C:\Temp\Downloads\2026\07\15\xml\factura002.xml'.
2026-07-15 10:20:05 | ERROR    | s3_extraction_service | Fallo procesando 'fractar/unprocessed/2026/07/15/xml/factura002.xml': No se pudo conectar al servidor SFTP 192.168.1.20:22: [Errno 110] Connection timed out
Traceback (most recent call last):
  ...
2026-07-15 10:20:05 | INFO     | s3_extraction_service | Fin de ejecución. Tiempo total=4.12s | encontrados=2 descargados=2 subidos=1 confirmados=1 fallidos=1
$ echo $?
0
```

El archivo `factura002.xml` queda intacto en S3 (nunca se confirmó
`processed`) y se reintentará en la siguiente ejecución del cron. El código
de salida es `0` porque el *proceso* terminó (se intentaron ambos
archivos); solo un error crítico (por ejemplo, no poder conectar el SFTP
**para todo el lote**, o no poder consultar `pending`) produce un código
distinto de cero:

```
$ python app/main.py
2026-07-15 10:25:01 | INFO     | s3_extraction_service | Inicio de ejecución de S3 Extraction Service.
2026-07-15 10:25:31 | ERROR    | s3_extraction_service | Error crítico: no se pudo completar el proceso (tiempo=30.02s): HTTP 503: Service Unavailable
Traceback (most recent call last):
  ...
$ echo $?
1
```

## Logging

Cada ejecución registra: inicio, fin, tiempo total, archivos encontrados,
descargados, subidos y confirmados, y cualquier error (con `Traceback`
completo vía `exc_info=True`). Se escribe simultáneamente en:

- **Archivo** (`LOG_FILE`, por defecto `logs/service.log`), con rotación
  automática a 5 MB por archivo y 5 respaldos históricos
  (`logging.handlers.RotatingFileHandler`).
- **Consola** (stdout), útil cuando el cron redirige la salida a su propio
  log (`>> cron.log 2>&1`).

## Manejo de errores y reintentos

Toda operación de red (llamadas al API, descarga por HTTPS, conexión y
subida SFTP) usa [`tenacity`](https://tenacity.readthedocs.io/) para
reintentar hasta `MAX_RETRIES` veces (por defecto 3), esperando
`RETRY_WAIT_SECONDS` (por defecto 5) segundos entre intentos, **solo** ante
errores transitorios: timeouts, problemas de conexión, o respuestas `5xx`
del API. Los errores de negocio 4xx del API (parámetros inválidos, archivo
no encontrado) **no** se reintentan: son definitivos.

Dos niveles de fallo, con distinto efecto:

| Nivel | Ejemplos | Efecto |
|---|---|---|
| **Por archivo** | Falla la descarga, el tamaño no coincide, falla la subida SFTP de ese archivo, el API rechaza `processed` | Se registra el error, el archivo local **no** se elimina, y se continúa con el siguiente archivo. Código de salida `0` si el resto del proceso terminó. |
| **Crítico** | Configuración inválida (`.env` incompleto), falla `pending` tras agotar reintentos, falla la conexión SFTP tras agotar reintentos | Se registra con stacktrace completo y el proceso termina con código de salida distinto de `0`. Ningún archivo del lote se pierde: como no se confirmó ninguno como `processed`, todos siguen pendientes para el siguiente ciclo. |

## Buenas prácticas aplicadas

- Python 3.12, tipado completo (`from __future__ import annotations` +
  anotaciones en toda función/método) y PEP 8.
- Diseño orientado a objetos con inyección de dependencias: `main.py`
  construye `ApiClient`, `DownloadManager` y `SftpClient` y los inyecta en
  `ExtractionService`, que no conoce sus implementaciones concretas.
  Facilita sustituir cualquiera de ellos (por ejemplo, para pruebas
  unitarias con mocks, o para agregar un nuevo destino de entrega).
- Excepciones de dominio propias (`ConfigError`, `ApiError`, `SftpError`)
  en vez de excepciones genéricas, para distinguir errores de negocio de
  errores de infraestructura.
- Cada módulo tiene una única responsabilidad (config, API, descarga,
  SFTP, logging, orquestación, entrypoint).
- Docstrings en cada módulo, clase y método público.

## Ampliaciones futuras

El desacoplamiento por inyección de dependencias facilita, sin tocar
`service.py`:

- Añadir un segundo destino de entrega (por ejemplo, otro SFTP o un share
  de red) implementando la misma interfaz que `SftpClient`.
- Cambiar la estrategia de reintentos (backoff exponencial en vez de
  espera fija) ajustando únicamente `api_client.py`, `download_manager.py`
  y `sftp_client.py`.
- Añadir métricas o alertas (por ejemplo, notificar si `files_failed > 0`)
  a partir del `ExecutionSummary` que retorna `ExtractionService.run()`.
