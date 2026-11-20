import json
import os
import base64
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo('America/Mexico_City')

BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', '')
REGION = os.environ.get('REGION', 'us-east-1')

s3_client = boto3.client('s3', region_name=REGION)


def _response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body, ensure_ascii=False),
    }


def _list_s3(prefix=''):
    """Lista carpetas y archivos bajo un prefijo dado."""
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix, Delimiter='/')

    folders = []
    files = []

    for page in pages:
        for common_prefix in page.get('CommonPrefixes') or []:
            folders.append(common_prefix['Prefix'])

        for obj in page.get('Contents') or []:
            key = obj['Key']
            # Excluir el propio prefijo "carpeta" que aparece como objeto
            if key == prefix:
                continue
            files.append({
                'key': key,
                'size': obj['Size'],
                'last_modified': obj['LastModified'].isoformat(),
            })

    return {'folders': folders, 'files': files}


def _build_s3_key(base_name):
    now = datetime.now(LOCAL_TZ)
    year  = now.strftime('%Y')
    month = now.strftime('%m')
    day   = now.strftime('%d')
    time  = now.strftime('%H%M%S')
    name  = base_name.removesuffix('.csv')
    return f"fractal/unprocessed/{year}/{month}/{day}/{name}_{time}.csv"


def _upload_csv(file_base64, base_name):
    try:
        file_bytes = base64.b64decode(file_base64)
    except Exception:
        raise ValueError('El campo file_base64 no es un base64 válido.')

    s3_key = _build_s3_key(base_name)

    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=file_bytes,
        ContentType='text/csv',
    )

    return {
        'bucket': BUCKET_NAME,
        'key': s3_key,
        'size': len(file_bytes),
    }


def s3_manager(event, context):
    try:
        if 'body' in event and event['body']:
            body = json.loads(event['body'])
        else:
            body = event

        action = body.get('action', '').strip().lower()

        if not action:
            return _response(400, {'message': 'El campo "action" es requerido. Valores válidos: list, upload.'})

        # ── ACCIÓN: list ──────────────────────────────────────────────
        if action == 'list':
            prefix = body.get('prefix', '')
            result = _list_s3(prefix)
            return _response(200, {'message': 'Listado obtenido correctamente.', 'data': result})

        # ── ACCIÓN: upload ────────────────────────────────────────────
        elif action == 'upload':
            file_base64 = body.get('file_base64')
            file_name = body.get('file_name')

            if not file_base64:
                return _response(400, {'message': 'El campo "file_base64" es requerido para la acción upload.'})
            if not file_name:
                return _response(400, {'message': 'El campo "file_name" es requerido para la acción upload.'})
            if not file_name.endswith('.csv'):
                return _response(400, {'message': 'El campo "file_name" debe terminar en .csv.'})

            result = _upload_csv(file_base64, file_name)
            return _response(200, {'message': f'Archivo "{file_name}" guardado correctamente en S3.', 'data': result})

        else:
            return _response(400, {'message': f'Acción "{action}" no reconocida. Valores válidos: list, upload.'})

    except ValueError as e:
        return _response(400, {'message': str(e)})
    except ClientError as e:
        error_code = e.response['Error']['Code']
        print(f'[S3 ClientError] {error_code}: {e}')
        return _response(500, {'message': f'Error de S3: {error_code}. Revisa los permisos o el nombre del bucket.'})
    except json.JSONDecodeError:
        return _response(400, {'message': 'El body de la solicitud no es un JSON válido.'})
    except Exception as e:
        print(f'[Error inesperado] {e}')
        return _response(500, {'message': f'Error interno: {str(e)}'})
