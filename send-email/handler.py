import json
import boto3
import base64
import os
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

def send_email_with_image(event, context):
    try:
        # Parsear el cuerpo de la solicitud
        if 'body' not in event or event['body'] is None:
             return {
                "statusCode": 400,
                "body": json.dumps({"message": "Valid JSON body is required"})
            }

        body_data = json.loads(event['body'])
        
        # Obtener configuración de variables de entorno
        SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'email')
        EMAIL_SUBJECT = os.environ.get('EMAIL_SUBJECT', 'Image Email')

        sender_email = body_data.get('sender_email', SENDER_EMAIL)
        to_address = body_data.get('to_address')
        subject = body_data.get('subject', EMAIL_SUBJECT)
        body_text = body_data.get('body', 'Here is the image you requested.')
        image_data_base64 = body_data.get('image_data') # Base64 string
        image_name = body_data.get('image_name', 'image.png')

        if not sender_email or not to_address or not image_data_base64:
             return {
                "statusCode": 400,
                "body": json.dumps({"message": "sender_email (env or body), to_address, and image_data are required"})
            }

        # Configurar cliente SES
        ses_client = boto3.client('ses')

        # Crear el objeto de mensaje MIME
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = to_address

        # Cuerpo del mensaje
        msg.attach(MIMEText(body_text, 'plain'))

        # Decodificar la imagen y adjuntarla
        try:
            image_bytes = base64.b64decode(image_data_base64)
            part = MIMEApplication(image_bytes)
            part.add_header('Content-Disposition', 'attachment', filename=image_name)
            msg.attach(part)
        except Exception as e:
             return {
                "statusCode": 400,
                "body": json.dumps({"message": f"Error decoding image: {str(e)}"})
            }

        # Enviar el correo
        response = ses_client.send_raw_email(
            Source=sender_email,
            Destinations=[to_address],
            RawMessage={
                'Data': msg.as_string(),
            }
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Email sent successfully",
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
