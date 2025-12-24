import datetime
import json
from email.mime.multipart import MIMEMultipart

from DB import DBConnection


def get_return_message(http_status_code, data):
    status = "error"
    if http_status_code == 200:
        status = "success"
    if data is None:
        data = {}
    data["status"] = status
    return {
        "statusCode": http_status_code,
        "headers": {
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'OPTIONS,POST'
        },
        "body": json.dumps(data, default=str)
    }

def filter_data(event, context):
    if event["body"] is None:
        return get_return_message(400, {"message":"No data provided"})
    request = json.loads(event["body"])
    filter = {}
    category = ""
    if "category" not in request:
        filter["category"] = ""
    else:
        filter["category"] = str(request["category"]).lower()

    if "filter_search" not in request or request["filter_search"] == "":
        filter["filter_search"]=""
    if "filter_operator" not in request or request["filter_operator"] == "":
        filter["filter_operator"] = ""
    else: filter["filter_operator"] = request["filter_operator"]
    if "filter_value" not in request or request["filter_value"] == "":
        filter["filter_value"] = ""
    else:
        filter["filter_value"] = float(request["filter_value"])
    if "filter_osi" not in request or len(request["filter_osi"]) == 0:
        filter["filter_osi"] = ""
    else:
        filter["filter_osi"] = request["filter_osi"]
    if "filter_search" not in request or request["filter_search"] == "":
        filter["filter_search"] = ""
    else:
        filter["filter_search"] = request["filter_search"]
    if "filter_date_start" not in request or request["filter_date_start"] == "":
        return get_return_message(400, {"message":"No filter_date_start provided"})
    else:
        filter["filter_date_start"] = request["filter_date_start"][0:10] + " 00:00:00"
    if "filter_date_end" not in request or request["filter_date_end"] == "":
        filter["filter_date_end"] = ""
        return get_return_message(400, {"message":"No filter_date_start provided"})
    else:
        filter["filter_date_end"] = request["filter_date_end"][0:10]+" 23:59:59"


    result = DBConnection.get_data_from_signal_type(
        request["signal_type"],
        request["items_per_page"],
        request["page"],
        filter
    )
    rows = {"data":result}
    return get_return_message(200, rows)

def get_signals(event, context):
    if event["body"] is None:
        return get_return_message(400, {"message":"No data provided"})
    request = json.loads(event["body"])

    type = ""
    subtype = ""
    name = ""
    if "filter_type" in request:
        type = str(request["filter_type"]).lower()
    if "filter_subtype" in request:
        subtype = str(request["filter_subtype"]).lower()
    if "filter_name" in request:
        name = str(request["filter_name"]).lower()
    print(type, subtype, name)
    rows = {
        "data":DBConnection.get_catalog(type, subtype, name),
        "customers":DBConnection.get_customers()
    }

    return get_return_message(200, rows)

def send_error_message(event, context):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from string import Template
    from botocore.exceptions import ClientError
    from templates.notification import html as html_template_message
    import boto3

    if event["body"] is None:
        return get_return_message(400, {"message":"No data provided"})
    errors = json.loads(event["body"])
    print(errors)
    body = ""
    for error in errors["text"]:
        body += f"<p>{error["time"]}</p>"
    from_email = "Alertas Doble BP <alertas_doble_bp@ammper.com>"
    to_emails = "alertas_doble_bp@ammper.com"


    charset = "utf-8"

    msg = MIMEMultipart('mixed')

    # Add subject, from and to lines.
    msg['Subject'] = "Error Doble BP"
    subject = msg["Subject"]
    msg['From'] = from_email
    msg['To'] = to_emails

    msg_body = MIMEMultipart('alternative')
    body_html = Template(html_template_message).substitute(
        title="RTU Communication Failure",
        message=body
    )

    # Encode the text and HTML content and set the character encoding. This step is
    # necessary if you're sending a message with characters outside the ASCII range.
    textpart = MIMEText(subject.encode(charset), 'plain', charset)
    htmlpart = MIMEText(body_html.encode(charset), 'html', charset)

    # Add the text and HTML parts to the child container.
    msg_body.attach(textpart)
    msg_body.attach(htmlpart)

    msg.attach(msg_body)


    # Add the attachment to the parent container.
    # msg.attach(att)
    message = []
    ses = boto3.client('ses')
    try:
        message = ses.send_raw_email(
            Source=from_email,
            Destinations=[to_emails],
            RawMessage={
                'Data': msg.as_string(),
            }
        )

    # Display an error if something goes wrong.
    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        print("Email sent! Message ID:"),
        print(message['MessageId'])
    return get_return_message(200, {"message":message})

