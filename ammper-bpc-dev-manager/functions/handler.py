import os
import json
import boto3

ec2 = boto3.client('ec2')

def start_ec2(event, context):
    instance_id = os.environ.get('INSTANCE_ID')
    
    print(f"Iniciando ejecución de start_ec2 (programada) para la instancia: {instance_id}")
        
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
        raise e

def stop_ec2(event, context):
    instance_id = os.environ.get('INSTANCE_ID')
    
    print(f"Iniciando ejecución de stop_ec2 (programada) para la instancia: {instance_id}")
    
    try:
        print(f"Ejecutando ec2.stop_instances para {instance_id}...")
        response = ec2.stop_instances(InstanceIds=[instance_id])
        print(f"Respuesta de AWS al apagar: {json.dumps(response, default=str)}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Apagando instancia EC2 {instance_id}',
                'response': str(response)
            })
        }
    except Exception as e:
        print(f"Error crítico al apagar la instancia {instance_id}: {str(e)}")
        raise e
