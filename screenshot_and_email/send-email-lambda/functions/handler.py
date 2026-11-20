import json
import boto3
import base64
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import os

SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'vrico@ammper.com')
EMAIL_SUBJECT = os.environ.get('EMAIL_SUBJECT', 'Captura de pantalla de minador')

def send_email(event, context):
    try:
        # Parsear el cuerpo de la solicitud
        if 'body' not in event or event['body'] is None:
             return {
                "statusCode": 400,
                "body": json.dumps({"message": "Valid JSON body is required"})
            }

        body_data = json.loads(event['body'])
        to_address = body_data.get('to_address', [SENDER_EMAIL])
        cc_addresses = body_data.get('cc', [])
        bcc_addresses = body_data.get('bcc', [])
        subject = body_data.get('subject', EMAIL_SUBJECT)
        body_text = body_data.get('body', 'Hola \n\n Adjunto evidencia del minador  \n\n Saludos,')
        image_data_base64 = body_data.get('image_data') # Base64 string
        image_name = body_data.get('image_name', 'minador_screenshot.png')

        if not to_address or not image_data_base64:
             return {
                "statusCode": 400,
                "body": json.dumps({"message": "to_address, and image_data are required"})
            }

        # Configurar cliente SES
        ses_client = boto3.client('ses')

        # Crear el objeto de mensaje MIME
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg["To"] = ", ".join(to_address)

        if cc_addresses:
            msg["Cc"] = ", ".join(cc_addresses)

        # Cuerpo del mensaje
        msg.attach(MIMEText(body_text, 'plain'))

        # Decodificar la imagen y adjuntarla
        try:
            image_bytes = base64.b64decode(image_data_base64)
            part = MIMEApplication(image_bytes)
            part.add_header('Content-Disposition', 'attachment', filename=image_name)
            msg.attach(part)
            destinations = to_address + cc_addresses + bcc_addresses

        except Exception as e:
             return {
                "statusCode": 400,
                "body": json.dumps({"message": f"Error al decodificar la imagen: {str(e)}"})
            }

        # Enviar el correo
        response = ses_client.send_raw_email(
            Source = SENDER_EMAIL,
            Destinations = destinations,
            RawMessage={
                'Data': msg.as_string(),
            }
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Email enviado correctamente",
                "messageId": response['MessageId']
            })
        }

    except ClientError as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"message": f"AWS SES Error: {e.response['Error']['Message']}"})
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"message": f"Internal Server Error: {str(e)}"})
        }