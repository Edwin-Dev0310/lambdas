import json
import os
import boto3
from templates.renderer import render_template

# --- CONFIGURACIÓN ---
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'digital@ammperpower.com')
NOTIFICATION_EMAIL = os.environ.get('NOTIFICATION_EMAIL', 'info@ammper.com')
REPLY_EMAIL = os.environ.get('REPLY_EMAIL', SENDER_EMAIL)

ses_client = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'us-east-1')) 

def build_emails(data):
    name = data.get('name', 'N/A')
    email = data.get('email', 'N/A')
    phone = data.get('phone', 'N/A')
    requested_service = data.get('requested_service', 'N/A')

    variables = {
        'name': name,
        'email': email,
        'phone': phone,
        'service': requested_service,
        'reply_email': REPLY_EMAIL,
    }

    notification_text = f"Contacto Ammper Power\n\nNombre: {name}\nEmail: {email}\nTeléfono: {phone}\nServicio Solicitado: {requested_service}\n"
    first_contact_text = f"Thank you, {name}. We've received your information.\n\nA member of our team will get back to you shortly.\n\nBest regards,\nThe Ammper Power Team\n"

    return [
        {
            'to': NOTIFICATION_EMAIL,
            'subject': 'New contact request – Ammper Power',
            'html': render_template('notification_form.html', variables),
            'text': notification_text,
        },
        {
            'to': email,
            'subject': 'Thank you for contacting Ammper Power',
            'html': render_template('first_contact.html', variables),
            'text': first_contact_text,
        },
    ]

def send_email(event, context):
    try:
        if 'body' in event:
             body = json.loads(event['body'])
        else:
             body = event
             
        name = body.get('name')
        email = body.get('email')
        phone = body.get('phone')
        requested_service = body.get('requested_service')
        
        if not all([name, email, phone, requested_service]):
             return {
                'statusCode': 400,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Credentials': True
                },
                'body': json.dumps({
                    'status': 'error',
                    'msg': 'Faltan campos requeridos en la solicitud (name, email, phone, requested_service).'
                })
            }
            
    except Exception as e:
        return {
            'statusCode': 400,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Credentials': True
            },
            'body': json.dumps({
                'status': 'error',
                'msg': 'Error al decodificar la solicitud.'
            })
        }

    try:
        for mail in build_emails(body):
            ses_client.send_email(
                Source=SENDER_EMAIL,
                Destination={
                    'ToAddresses': [mail['to']]
                },
                Message={
                    'Subject': {'Data': mail['subject'], 'Charset': 'UTF-8'},
                    'Body': {
                        'Text': {'Data': mail['text'], 'Charset': 'UTF-8'},
                        'Html': {'Data': mail['html'], 'Charset': 'UTF-8'}
                    }
                }
            )
        
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Credentials': True
            },
            'body': json.dumps({
                'status': 'success',
                'msg': 'Correo enviado exitosamente.'
            })
        }

    except Exception as e:
        print(f"Error al enviar correo con SES: {e}")
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Credentials': True
            },
            'body': json.dumps({
                'status': 'error',
                'msg': f'Error interno al procesar el envío de correo. Detalles: {str(e)}'
            })
        }
