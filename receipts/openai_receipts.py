import base64
import json
from django.conf import settings
from openai import OpenAI

SYSTEM_PROMPT = '''
Jesteś parserem polskich paragonów. Zwracasz wyłącznie poprawny JSON.
Rozpoznaj sklep, datę, godzinę, sumę, produkty, kategorie oraz promocje/oszczędności.
Jeśli czegoś nie wiesz, wpisz null albo pusty string.
'''

JSON_SCHEMA = {
    'name': 'receipt_ocr',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'merchant_name': {'type': 'string'},
            'purchased_at': {'type': ['string', 'null']},
            'total_amount': {'type': ['number', 'null']},
            'currency': {'type': 'string'},
            'payment_method': {'type': 'string'},
            'items': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
                'name': {'type': 'string'},
                'quantity': {'type': ['number', 'null']},
                'unit_price': {'type': ['number', 'null']},
                'paid_price': {'type': ['number', 'null']},
                'regular_price': {'type': ['number', 'null']},
                'discount_amount': {'type': ['number', 'null']},
                'promotion_name': {'type': 'string'},
                'is_discounted': {'type': 'boolean'},
                'category': {'type': 'string'},
            }, 'required': ['name', 'quantity', 'unit_price', 'paid_price', 'regular_price', 'discount_amount', 'promotion_name', 'is_discounted', 'category']}}
        },
        'required': ['merchant_name', 'purchased_at', 'total_amount', 'currency', 'payment_method', 'items']
    },
    'strict': True,
}


def parse_receipt_image(image_path: str) -> dict:
    client = OpenAI(api_key=settings.OPENAI_KEY)
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    response = client.chat.completions.create(
        model=settings.OPENAI_RECEIPT_MODEL,
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Przeanalizuj paragon i zwróć JSON.'},
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}
            ]},
        ],
        response_format={'type': 'json_schema', 'json_schema': JSON_SCHEMA},
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)
