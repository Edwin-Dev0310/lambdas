import json
import os
import boto3
from templates.notification import get_html_template

# --- CONFIGURACIÓN ---
DESTINATION_EMAIL = os.environ.get('DESTINATION_EMAIL', 'info@ammper.com')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'digital@ammperpower.com')

ses_client = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'us-east-1')) 

def generate_email_body(data):
    name = data.get('name', 'N/A')
    email = data.get('email', 'N/A')
    phone = data.get('phone', 'N/A')
    requested_service = data.get('requested_service', 'N/A')
    
    html_content = get_html_template(name, email, phone, requested_service)
    text_content = f"Contacto Ammper Power\n\nNombre: {name}\nEmail: {email}\nTeléfono: {phone}\nServicio Solicitado: {requested_service}\n"
    
    return html_content, text_content

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

    html_body, text_body = generate_email_body(body)
    subject = f"Contacto Ammper Power"

    try:
        ses_client.send_email(
            Source=SENDER_EMAIL,
            Destination={
                'ToAddresses': [DESTINATION_EMAIL]
            },
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {
                    'Text': {'Data': text_body, 'Charset': 'UTF-8'},
                    'Html': {'Data': html_body, 'Charset': 'UTF-8'}
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
