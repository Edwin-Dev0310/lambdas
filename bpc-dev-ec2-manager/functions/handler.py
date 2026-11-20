import os
import json
import boto3

ec2 = boto3.client('ec2')

def start_ec2(event, context):
    instance_id = os.environ.get('INSTANCE_ID')
    expected_token = os.environ.get('SECRET_PARAM')
    
    print(f"Iniciando ejecución de start_ec2 para la instancia: {instance_id}")
    
    # Obtener query params
    query_params = event.get('queryStringParameters') or {}
    token = query_params.get('token')
    
    # Validar el parametro secreto para dar seguridad al endpoint
    if token != expected_token:
        print(f"Intento fallido de encendido. Token recibido no coincide.")
        return {
            'statusCode': 403,
            'body': json.dumps({'message': 'Acceso denegado: parametro incorrecto o faltante'})
        }
        
    try:
        print(f"Ejecutando ec2.start_instances para {instance_id}...")
        response = ec2.start_instances(InstanceIds=[instance_id])
        print(f"Respuesta de AWS al encender: {json.dumps(response, default=str)}")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Iniciando instancia EC2 {instance_id}',
                'response': str(response)
            })
        }
    except Exception as e:
        print(f"Error crítico al encender la instancia {instance_id}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': 'Error al iniciar la instancia', 'error': str(e)})
        }
