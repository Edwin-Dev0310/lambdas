import os
from html import escape

TEMPLATES_DIR = os.path.dirname(os.path.abspath(__file__))


def render_template(filename, variables):
    with open(os.path.join(TEMPLATES_DIR, filename), encoding='utf-8') as f:
        html = f.read()

    for key, value in variables.items():
        html = html.replace('{{' + key + '}}', escape(str(value)))

    return html
