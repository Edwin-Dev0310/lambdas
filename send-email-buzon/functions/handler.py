# lambda_function_sin_recaptcha.py

import json
import os
import boto3

# --- CONFIGURACIÓN ---
DESTINATION_EMAIL = os.environ.get('DESTINATION_EMAIL', 'buzon@invexcapital.com')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'buzon@invexcapital.com')

# Inicializar el cliente SES
ses_client = boto3.client('ses', region_name=os.environ.get('AWS_REGION', 'us-east-1')) 

def generate_email_body(questions_data):
    """Genera el cuerpo del correo HTML y texto plano a partir del JSON de preguntas."""
    
    html_content = """
    <html>
    <body>
        <h1>🚨 ¡Atención! Nuevo Caso de Apoyo Recibido</h1>
        <p>Se ha generado un nuevo registro en el Buzón de Apoyo. Es necesario revisar y gestionar la solicitud enviada mediante el formulario. Los datos del usuario y sus respuestas son:</p>
        <table border="1" cellpadding="10" cellspacing="0" style="width:100%; border-collapse: collapse;">
            <thead>
                <tr style="background-color: #f2f2f2;">
                    <th>Preguntas</th>
                </tr>
            </thead>
            <tbody>
    """
    text_content = "Nuevo Buzón de Apoyo Recibido\n\nDetalles del Formulario:\n\n"

    # Itera sobre los datos, ordenados por ID (clave del diccionario)
    sorted_keys = sorted([int(k) for k in questions_data.keys()])
    
    for q_id in sorted_keys:
        item = questions_data[str(q_id)]
        question_text = item.get('pregunta', 'Pregunta sin texto')
        response_text = item.get('respuesta', 'N/A')
        
        # Añadir al HTML
        html_content += f"""
        <tr>
            <td style="font-weight: bold;">{question_text}</td>
        </tr>
        <tr>
            <td>R-. {response_text}</td>
        </tr>
        """
        # Añadir al texto plano
        text_content += f"- Pregunta {q_id}: {question_text}\n  Respuesta: {response_text}\n\n"

    html_content += """
            </tbody>
        </table>
    </body>
    </html>
    """
    return html_content, text_content

def send_email(event, context):
    """Función principal de Lambda."""
    
    # 1. Analizar el cuerpo de la solicitud
    try:
        if 'body' in event:
             body = json.loads(event['body'])
        else:
             body = event

        # Extraer finalLog (esperado del Front-end)
        final_log = body.get('finalLog')
        
        if not final_log or not isinstance(final_log, dict):
            return {
                'statusCode': 400,
                'body': json.dumps({'message': 'Error: Datos de formulario incompletos o mal formados.'})
            }
            
    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'body': json.dumps({'message': 'Error al decodificar el JSON de la solicitud.'})
        }
    
    # print("=========================================")
    # print("JSON de Preguntas Obtenidas (finalLog):")
    # print(json.dumps(final_log, indent=2))
    # print("=========================================")

    # NOTA: La verificación de reCAPTCHA fue eliminada aquí.

    # 3. Generar el contenido del correo
    html_body, text_body = generate_email_body(final_log)
    subject = "Nueva Solicitud Recibida: Buzón de Apoyo"

    # 4. Enviar el Correo vía SES
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
            'body': json.dumps({'message': 'Formulario recibido y correo de notificación enviado.'})
        }

    except Exception as e:
        print(f"Error al enviar correo con SES: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': f'Error interno al procesar el envío de correo. Detalles: {str(e)}'})
        }