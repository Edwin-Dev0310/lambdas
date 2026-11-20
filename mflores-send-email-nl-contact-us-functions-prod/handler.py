import os
import pg8000
import boto3
import json
data = [
    { "value": "Home", "template":"home" }
]

def get_template_by_value(value_to_find, data, default_template="default"):
    for item in data:
        if item["value"].lower() == value_to_find.lower():
            return item["template"].lower()
    return default_template

def read_html_file(template):
    try:
        with open('templates/'+template, 'r') as file:
            html_template = file.read()
            return html_template
    except FileNotFoundError:
        with open('templates/default.html', 'r') as file:
            html_template = file.read()
            return html_template
    except Exception as e:
        with open('templates/default.html', 'r') as file:
            html_template = file.read()
            return html_template


def ses_send_notification(recipient_email, body):

    language = body.get('cra24_Language') if body.get('cra24_Language') else 'ES'
    current_page = body.get('cra24_current_page')
    # Validar que el parámetro "to" esté presente
    if not recipient_email:
        raise ValueError("El campo 'email' es requerido")

    if current_page.lower() == "home":
        value_to_search = body.get('cra24_service')
        # template = get_template_by_value(value_to_search, data) //Descomentar para varios correos
        template = current_page.lower()
    else:
        template = current_page.lower()

    template = template +'_'+language.upper()+'.html'

    # Leer el archivo de plantilla HTML
    html_template = read_html_file(template)
    flag=""
    html_content = html_template.replace('{{name}}', flag)

    # ses_email_from = 'mflores@ammper.com'
    ses_email_from = 'info@ammper.nl'
    ses_client = boto3.client('ses')

    response = ses_client.send_email(
        Source = ses_email_from,
        Destination = {
            'ToAddresses': [recipient_email] #recipient_email,
        },
        Message = {
            'Subject': {
                'Data': 'Ammper - Contact Us',
                'Charset': 'UTF-8'
            },
            'Body': {
                'Html': {
                    'Data': html_content,
                    'Charset': 'UTF-8'
                }
            }
        }
    )

    return response


#lambda principal
def send_email(event, context):
    body = json.loads(event['body'])
    to_email = body.get('emailaddress1')
    
    try:
        prueba = ses_send_notification(to_email, body)
    except Exception as e:
        error_msg = f"\n => ERROR: \n\n {e}..."
        print(error_msg)
        exit(1)

    return {
        "statusCode": 200,
        "headers": {
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'OPTIONS,POST'
        },
        "body": json.dumps({
            "status": "success",
            "message": "Correo enviado correctamente.",
        }),
    }