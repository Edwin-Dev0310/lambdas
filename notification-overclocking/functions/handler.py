import csv
import io
import json
import os
import boto3
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from botocore.exceptions import ClientError

BUCKET_NAME  = os.environ.get('S3_BUCKET_NAME', 'test-edwin-notificaciones')
S3_PREFIX    = os.environ.get('S3_PREFIX', 'mina2_offer_dispatch/overclocking/dispatch_overclocking/')
REGION       = os.environ.get('REGION', 'us-east-1')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', '')

def _parse_emails(env_key):
    return [e.strip() for e in os.environ.get(env_key, '').split(',') if e.strip()]

SUCCESS_TO  = _parse_emails('SUCCESS_TO_EMAILS')
SUCCESS_CC  = _parse_emails('SUCCESS_CC_EMAILS')
SUCCESS_BCC = _parse_emails('SUCCESS_BCC_EMAILS')

FAILURE_TO  = _parse_emails('FAILURE_TO_EMAILS')
FAILURE_CC  = _parse_emails('FAILURE_CC_EMAILS')
FAILURE_BCC = _parse_emails('FAILURE_BCC_EMAILS')

s3_client  = boto3.client('s3', region_name=REGION)
ses_client = boto3.client('ses', region_name=REGION)


CST = timezone(timedelta(hours=-6))

def _next_day_s3_key():
    tomorrow  = datetime.now(CST) + timedelta(days=1)
    file_name = f"output_data_{tomorrow.strftime('%Y-%m-%d')}.csv"
    s3_key    = f"{S3_PREFIX}{file_name}"
    return s3_key, file_name


def _fetch_s3_file(s3_key):
    obj = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
    return obj['Body'].read()


def _transform_csv(csv_bytes):
    """
    Valida columnas A=date, B=hour y la existencia de 'offer_d MW'.
    Retorna CSV con columnas: date, hour, on_off.
    on_off = 1 si offer_d MW > 0, de lo contrario 0.
    """
    content = csv_bytes.decode('utf-8-sig')
    reader  = csv.DictReader(io.StringIO(content))
    headers = list(reader.fieldnames or [])

    if len(headers) < 2:
        raise ValueError("El CSV no tiene suficientes columnas.")
    if headers[0] != 'date':
        raise ValueError(f'Columna A esperada: "date", encontrada: "{headers[0]}".')
    if headers[1] != 'hour':
        raise ValueError(f'Columna B esperada: "hour", encontrada: "{headers[1]}".')
    if 'offer_d MW' not in headers:
        raise ValueError('No se encontro la columna "offer_d MW".')
    if 'overclocking' not in headers:
        raise ValueError('No se encontro la columna "overclocking".')

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['date', 'hour', 'on_off', 'overclocking'])
    writer.writeheader()

    for row in reader:
        try:
            offer_val = float(row.get('offer_d MW') or 0)
        except (ValueError, TypeError):
            offer_val = 0.0

        writer.writerow({
            'date':   row['date'],
            'hour':   row['hour'],
            'on_off': 1 if offer_val > 0 else 0,
            'overclocking':   row['overclocking'],
        })

    return output.getvalue().encode('utf-8')


def _send_with_attachment(subject, body_html, body_text, attachment_bytes, attachment_name, to, cc, bcc):
    msg            = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From']    = SENDER_EMAIL
    msg['To']      = ', '.join(to)
    if cc:
        msg['Cc'] = ', '.join(cc)

    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(body_text, 'plain', 'utf-8'))
    alt.attach(MIMEText(body_html, 'html',  'utf-8'))
    msg.attach(alt)

    att = MIMEBase('text', 'csv')
    att.set_payload(attachment_bytes)
    encoders.encode_base64(att)
    att.add_header('Content-Disposition', 'attachment', filename=attachment_name)
    msg.attach(att)

    ses_client.send_raw_email(
        Source=SENDER_EMAIL,
        Destinations=to + cc + bcc,
        RawMessage={'Data': msg.as_string()},
    )


def _send_plain(subject, body_html, body_text, to, cc, bcc):
    destination = {'ToAddresses': to}
    if cc:
        destination['CcAddresses'] = cc
    if bcc:
        destination['BccAddresses'] = bcc

    ses_client.send_email(
        Source=SENDER_EMAIL,
        Destination=destination,
        Message={
            'Subject': {'Data': subject, 'Charset': 'UTF-8'},
            'Body': {
                'Text': {'Data': body_text, 'Charset': 'UTF-8'},
                'Html': {'Data': body_html, 'Charset': 'UTF-8'},
            },
        },
    )


def _success_email_content(file_name):
    subject   = f"[Overclocking Dispatch] Archivo disponible: {file_name}"
    body_text = (
        f"Se encontro y proceso correctamente el archivo: {file_name}\n\n"
        "Se adjunta el CSV transformado con las columnas: date, hour, on_off, overclocking.\n\n"
        "  - on_off = 1  cuando  offer_d MW > 0\n"
        "  - on_off = 0  cuando  offer_d MW = 0\n"
    )
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
      <h2 style="color:#2E7D32;">Overclocking Dispatch &mdash; Archivo disponible</h2>
      <p>Se encontr&oacute; y proces&oacute; correctamente el archivo:</p>
      <p style="font-size:15px;font-weight:bold;">{file_name}</p>
    </body></html>
    """
    # <p>Se adjunta el CSV transformado con las columnas:
    #      <code>date</code>, <code>hour</code>, <code>on_off</code>.</p>
    #   <table border="1" cellpadding="8" cellspacing="0"
    #          style="border-collapse:collapse;width:300px;">
    #     <thead style="background:#f5f5f5;">
    #       <tr><th>on_off</th><th>Condici&oacute;n</th></tr>
    #     </thead>
    #     <tbody>
    #       <tr><td>1</td><td>offer_d MW &gt; 0</td></tr>
    #       <tr><td>0</td><td>offer_d MW = 0</td></tr>
    #     </tbody>
    #   </table>
    return subject, body_html, body_text


def _failure_email_content(searched_file, detail):
    subject   = f"[Overclocking Dispatch] ALERTA - Archivo no encontrado: {searched_file}"
    body_text = (
        f"No se encontro el archivo esperado para el dia siguiente:\n\n"
        f"  Archivo buscado : {searched_file}\n"
        f"  Bucket          : {BUCKET_NAME}\n"
        f"  Path            : {S3_PREFIX}\n\n"
        f"Detalle del error:\n{detail}\n\n"
        "Por favor revise el proceso de generacion de archivos."
    )
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
      <h2 style="color:#C62828;">Overclocking Dispatch &mdash; Alerta</h2>
      <p>No se encontr&oacute; el archivo esperado para el d&iacute;a siguiente:</p>
      <table border="1" cellpadding="8" cellspacing="0"
             style="border-collapse:collapse;width:500px;">
        <tr><td><strong>Archivo buscado</strong></td><td>{searched_file}</td></tr>
        <tr><td><strong>Bucket</strong></td><td>{BUCKET_NAME}</td></tr>
        <tr><td><strong>Path</strong></td><td>{S3_PREFIX}</td></tr>
        <tr><td><strong>Detalle</strong></td><td>{detail}</td></tr>
      </table>
      <p>Por favor revise el proceso de generaci&oacute;n de archivos.</p>
    </body></html>
    """
    return subject, body_html, body_text


def notify_overclocking(event, context):
    s3_key, file_name = _next_day_s3_key()
    print(json.dumps({'action': 'searching', 'bucket': BUCKET_NAME, 'key': s3_key}))

    # --- Intentar obtener el archivo ---
    try:
        csv_bytes = _fetch_s3_file(s3_key)
    except ClientError as e:
        code = e.response['Error']['Code']
        if code in ('NoSuchKey', '404'):
            detail  = f"El archivo no existe en s3://{BUCKET_NAME}/{s3_key}"
            subject, body_html, body_text = _failure_email_content(file_name, detail)
            _send_plain(subject, body_html, body_text, FAILURE_TO, FAILURE_CC, FAILURE_BCC)
            print(json.dumps({'action': 'file_not_found', 'key': s3_key}))
            return {'statusCode': 200, 'body': 'Archivo no encontrado. Notificacion enviada.'}
        raise

    # --- Transformar CSV ---
    try:
        transformed_bytes = _transform_csv(csv_bytes)
    except ValueError as e:
        detail  = str(e)
        subject, body_html, body_text = _failure_email_content(file_name, detail)
        _send_plain(subject, body_html, body_text, FAILURE_TO, FAILURE_CC, FAILURE_BCC)
        print(json.dumps({'action': 'transform_error', 'detail': detail}))
        return {'statusCode': 200, 'body': f'Error al transformar CSV. Notificacion enviada.'}

    # --- Enviar correo con adjunto ---
    subject, body_html, body_text = _success_email_content(file_name)
    _send_with_attachment(subject, body_html, body_text, transformed_bytes, file_name,
                          SUCCESS_TO, SUCCESS_CC, SUCCESS_BCC)
    print(json.dumps({'action': 'email_sent', 'file': file_name}))
    return {'statusCode': 200, 'body': f'Proceso completado. Email enviado con {file_name}.'}
