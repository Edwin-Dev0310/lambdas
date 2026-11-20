# S3 Extraction Gateway Lambda

API serverless (API Gateway + Lambda Python 3.12 + S3) que expone un
servicio de extracción de archivos pendientes en un bucket S3, sin que el
cliente tenga acceso directo al bucket ni a credenciales de AWS. La Lambda
opera exclusivamente con las credenciales temporales de su IAM Role.

Es el backend consumido por el cliente de consola **S3 Extraction Service**
(`S3-Extraction-Service/`), pensado para ejecutarse cada 5 minutos vía
cron/Task Scheduler y entregar los archivos por SFTP a un tercero.

## Arquitectura

```
Cron / Task Scheduler --> S3 Extraction Service --> HTTPS --> API Gateway
                                                                   |
                                                                   v
                                                              AWS Lambda
                                                                   |
                                                                   v
                                                               Amazon S3
```

El proyecto está organizado en dos stacks de Serverless Framework
independientes, siguiendo el mismo patrón usado en `base_lambda/` y en
`s3-file-gateway-lambda/`:

- **`iam/`**: crea el IAM Role y la Managed Policy con permisos mínimos.
- **`functions/`**: crea la Lambda y el API Gateway, referenciando el Role
  del stack `iam` por ARN (no por `Fn::GetAtt`), por lo que **`iam/` debe
  desplegarse primero**.

## Estructura del proyecto

```
s3-extraction-gateway-lambda/
├── iam/
│   ├── serverless.yml
│   ├── roles.yml              # LambdaS3ExtractionGatewayRole
│   └── policies.yml           # LambdaS3ExtractionGatewayPolicy (permisos minimos)
├── functions/
│   ├── serverless.yml         # Lambda + API Gateway + API Key
│   ├── requirements.txt
│   ├── handler.py              # Entrypoint: enrutamiento y manejo de errores HTTP
│   ├── config.py               # Carga y validacion de variables de entorno
│   ├── errors.py               # ValidationError, NotFoundError, ConfigError
│   ├── logging_utils.py        # Logging estructurado (JSON) hacia CloudWatch
│   ├── responses.py            # Construccion de respuestas API Gateway proxy
│   ├── id_codec.py             # Codificacion/decodificacion del campo 'id'
│   ├── s3_service.py           # Operaciones S3: listar pendientes (+URL) / mover
│   └── validators.py           # Validacion de payloads por accion
├── events/                     # Eventos de ejemplo para la consola de Lambda
│   ├── pending_event.json
│   ├── pending_missing_prefix_event.json
│   ├── pending_invalid_prefix_event.json
│   ├── processed_event.json
│   ├── processed_custom_prefix_event.json
│   ├── processed_missing_id_event.json
│   ├── processed_invalid_id_event.json
│   ├── missing_action_event.json
│   └── api_gateway_proxy_pending_event.json
├── tests/
│   ├── test_validators.py
│   ├── test_s3_service.py
│   └── test_id_codec.py
├── requirements-dev.txt
├── pytest.ini
└── README.md
```

## Variables de entorno

Configuradas en `functions/serverless.yml` (sección `custom.config.<stage>`),
inyectadas a la Lambda vía `provider.environment`:

| Variable | Descripción |
|---|---|
| `BUCKET_NAME` | Bucket S3 sobre el que opera el servicio. |
| `SOURCE_PREFIX` | Prefijo donde llegan los archivos pendientes (ej. `fractar/unprocessed/`). |
| `PROCESSED_PREFIX` | Prefijo destino por defecto al marcar un archivo como procesado (ej. `processed/`). El cliente puede sobreescribirlo por llamada con `processedPrefix` en la acción `processed`. |
| `URL_EXPIRATION_SECONDS` | Vigencia en segundos de las URLs prefirmadas de descarga. |

No se define ninguna credencial de AWS: la Lambda usa únicamente su IAM Role.

## Contrato de la API

`POST /files` (requiere header `x-api-key`). El cuerpo es un JSON con un
campo `action`.

### 1. `pending`

Devuelve **todos** los archivos actualmente presentes bajo `SOURCE_PREFIX`
(no filtra por fecha), cada uno con su URL prefirmada de descarga ya
incluida, para que el cliente no necesite una segunda llamada.

El campo `prefix` recibido debe coincidir exactamente con el `SOURCE_PREFIX`
configurado en la Lambda (por seguridad: el cliente no puede pedir un
prefijo arbitrario del bucket).

Entrada:
```json
{ "action": "pending", "prefix": "fractar/unprocessed/" }
```

Respuesta (200):
```json
{
  "requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3",
  "success": true,
  "files": [
    {
      "id": "ZnJhY3Rhci91bnByb2Nlc3NlZC8yMDI2LzA3LzE1L3htbC9mYWN0dXJhMDAxLnhtbA",
      "key": "fractar/unprocessed/2026/07/15/xml/factura001.xml",
      "relativePath": "2026/07/15/xml/factura001.xml",
      "fileName": "factura001.xml",
      "size": 25478,
      "lastModified": "2026-07-15T10:20:00Z",
      "downloadUrl": "https://..."
    }
  ]
}
```

### 2. `processed`

Confirma el procesamiento de un archivo: mueve el objeto de `SOURCE_PREFIX`
al prefijo de procesados, conservando su ruta relativa (copy → verificar
destino → delete origen).

Por defecto el destino es el `PROCESSED_PREFIX` configurado en la Lambda,
pero el cliente puede elegir un prefijo distinto **en cada llamada**
enviando el campo opcional `processedPrefix`:

Entrada (destino por defecto, `PROCESSED_PREFIX`):
```json
{
  "action": "processed",
  "requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3",
  "id": "ZnJhY3Rhci91bnByb2Nlc3NlZC8yMDI2LzA3LzE1L3htbC9mYWN0dXJhMDAxLnhtbA"
}
```

Entrada (destino elegido por el cliente):
```json
{
  "action": "processed",
  "requestId": "8d5d5c5d-a102-4d65-bf76-f58d12a9e8a3",
  "id": "ZnJhY3Rhci91bnByb2Nlc3NlZC8yMDI2LzA3LzE1L3htbC9mYWN0dXJhMDAxLnhtbA",
  "processedPrefix": "archivados/2026/"
}
```

En ambos casos, el archivo se mueve a `{processedPrefix}{relativePath}`
dentro del **mismo bucket** (`BUCKET_NAME`); no es posible mover a otro
bucket. `processedPrefix` no puede ser igual a `SOURCE_PREFIX` (400).

Respuesta (200): `{ "requestId": "...", "success": true }`
Si el origen no existe: **404**. Si la copia no puede verificarse en el
destino, se conserva el original y se responde **500** (no se pierde el
archivo).

### Diseño del campo `id` (sin estado adicional)

`id` es la propia `key` de S3 codificada en Base64 URL-safe
(`id_codec.py`). La acción `processed` decodifica `id` directamente a la
`key` original, sin necesitar una base de datos (DynamoDB u otra) que
recuerde qué `id` corresponde a qué archivo entre la llamada `pending` y la
llamada `processed`.

Esto es seguro porque no oculta nada que el cliente no tenga ya: la misma
`key` viaja en texto plano en el propio campo `key` de la respuesta de
`pending`. `requestId` se usa únicamente para trazabilidad/logging (se
genera un UUID por cada llamada a `pending` y se devuelve igual en
`processed`), no forma parte de la resolución del archivo.

Si en el futuro se requiere invalidar `id`s antiguos, revocar acceso, o
llevar una auditoría persistente de qué se listó y qué se confirmó, este
diseño puede evolucionar a un `id` firmado (HMAC) con expiración, o a un
registro en DynamoDB — ambas opciones son compatibles con el mismo contrato
de API expuesto al cliente.

### Validaciones y códigos HTTP

| Código | Causa |
|---|---|
| 400 | Falta `action`, acción no soportada, falta `prefix`/`requestId`/`id`, `prefix` no coincide con `SOURCE_PREFIX`, `id` no es una codificación válida / no pertenece a `SOURCE_PREFIX`, `processedPrefix` vacío, o `processedPrefix` igual a `SOURCE_PREFIX`. |
| 404 | El archivo (resuelto desde `id`) no existe en el bucket. |
| 500 | Error de configuración de la Lambda o error inesperado (no expone detalles internos al cliente; el detalle completo queda en CloudWatch Logs). |

## Permisos IAM (mínimos, solo sobre el bucket configurado)

```
s3:ListBucket   -> arn:aws:s3:::<bucket>
s3:GetObject    -> arn:aws:s3:::<bucket>/*
s3:PutObject    -> arn:aws:s3:::<bucket>/*
s3:DeleteObject -> arn:aws:s3:::<bucket>/*
```

Definidos en `iam/policies.yml` (`LambdaS3ExtractionGatewayPolicy`), adjuntos
al role `LambdaS3ExtractionGatewayRole` (`iam/roles.yml`) junto con la
managed policy `AWSLambdaBasicExecutionRole` (necesaria para escribir en
CloudWatch Logs).

## Despliegue

Requiere [Serverless Framework](https://www.serverless.com/) y el plugin
`serverless-python-requirements`:

```bash
npm install -g serverless
npm install --save-dev serverless-python-requirements
```

Antes de desplegar, reemplace `CAMBIAR_ACCOUNT_ID` y `CAMBIAR_BUCKET_NAME`
en `iam/serverless.yml` y `functions/serverless.yml`, y ajuste
`source_prefix`, `processed_prefix` y `url_expiration_seconds` según su
entorno.

**1. Desplegar el stack IAM primero** (crea el Role que `functions` referencia por ARN):

```bash
cd iam
serverless deploy --stage dev
cd ..
```

**2. Desplegar el stack de funciones:**

```bash
cd functions
serverless deploy --stage dev
```

Al finalizar, Serverless imprime la URL del endpoint
(`https://{api-id}.execute-api.{region}.amazonaws.com/dev/files`).

**3. Obtener la API Key** generada (`s3-extraction-gateway-dev-key`):

```bash
serverless info --stage dev --verbose | grep -A2 "api keys"
# o desde la consola AWS: API Gateway -> API Keys
```

Para desinstalar: `serverless remove --stage dev` en `functions/` primero,
luego en `iam/`.

## Probar la API con curl

```bash
# 1. Pedir archivos pendientes
curl -X POST "https://{api-id}.execute-api.{region}.amazonaws.com/dev/files" \
  -H "x-api-key: {su-api-key}" \
  -H "Content-Type: application/json" \
  -d '{"action":"pending","prefix":"fractar/unprocessed/"}'

# 2. Confirmar procesado (usar el 'id' y 'requestId' devueltos arriba)
curl -X POST "https://{api-id}.execute-api.{region}.amazonaws.com/dev/files" \
  -H "x-api-key: {su-api-key}" \
  -H "Content-Type: application/json" \
  -d '{"action":"processed","requestId":"{requestId}","id":"{id}"}'
```

## Probar desde la consola de Lambda

En la pestaña **Test** de la Lambda, cree un evento de prueba pegando el
contenido de cualquiera de los archivos en `events/`:

| Archivo | Qué prueba |
|---|---|
| `pending_event.json` | Listado exitoso de pendientes (200). |
| `pending_missing_prefix_event.json` | Falta `prefix` (400). |
| `pending_invalid_prefix_event.json` | `prefix` no coincide con `SOURCE_PREFIX` (400). |
| `processed_event.json` | Confirmar procesado con destino por defecto (200) — reemplace `id` por uno real obtenido de `pending`. |
| `processed_custom_prefix_event.json` | Confirmar procesado con `processedPrefix` elegido por el cliente (200) — reemplace `id` igual que arriba. |
| `processed_missing_id_event.json` | Falta `id` (400). |
| `processed_invalid_id_event.json` | `id` no es una codificación válida (400). |
| `missing_action_event.json` | Falta `action` (400). |
| `api_gateway_proxy_pending_event.json` | Mismo caso `pending`, simulando el evento real que envía API Gateway (proxy integration). |

El handler acepta ambos formatos indistintamente: el JSON "plano" (como en
los ejemplos de entrada de este documento) para pruebas rápidas desde la
consola, y el evento proxy completo de API Gateway (con `body` como cadena
JSON) para producción.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Los tests de `s3_service.py` usan [`moto`](https://github.com/getmoto/moto)
para simular S3 sin credenciales reales. `validators.py` e `id_codec.py` se
prueban de forma aislada (no dependen de AWS).

## Notas de diseño

- `pending` no filtra por fecha (a diferencia de la acción `list` de
  `s3-file-gateway-lambda`): devuelve todo lo que quede bajo `SOURCE_PREFIX`
  en el momento de la llamada, que es exactamente lo que un cron que corre
  cada 5 minutos necesita ("¿qué hay pendiente ahora?").
- `list_pending_files` genera la URL prefirmada en el mismo recorrido de
  paginación que usa para listar, sin una llamada `head_object` adicional
  por archivo: el objeto ya se sabe existente por venir del propio listado.
- `move_object` verifica la existencia del destino antes de eliminar el
  origen: ante cualquier fallo de la copia, el archivo original nunca se
  pierde.
- El logging usa formato JSON de una línea por evento (acción, estado,
  detalle), fácil de filtrar con CloudWatch Logs Insights.
