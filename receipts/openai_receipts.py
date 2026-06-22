import base64
import json
from decimal import Decimal, InvalidOperation
from django.conf import settings
from openai import OpenAI
from .categories import allowed_categories_prompt_text, infer_category_from_name


def _money(value):
    if value in (None, ''):
        return None
    try:
        return str(Decimal(str(value).replace(',', '.')).quantize(Decimal('0.01')))
    except (InvalidOperation, ValueError):
        return None


def _number(value):
    if value in (None, ''):
        return None
    try:
        return str(Decimal(str(value).replace(',', '.')))
    except (InvalidOperation, ValueError):
        return None


SYSTEM_PROMPT = f'''
Jesteś parserem polskich paragonów. Zwracasz wyłącznie poprawny JSON.
Rozpoznaj sklep, datę, godzinę, sumę, produkty, promocje i oszczędności.

Bardzo ważne:
- Zachowuj polskie znaki w nazwach produktów, kategorii i podkategorii.
- Każdy produkt musi mieć category i subcategory wybrane wyłącznie z poniższej listy.
- Nie wolno tworzyć własnych kategorii ani podkategorii.
- category i subcategory zwracaj dokładnie tak, jak są zapisane poniżej, razem z polskimi znakami.
- Używaj "Inne" wyłącznie wtedy, gdy naprawdę nie da się rozsądnie dopasować produktu.
- Oczywiste produkty dopasowuj konkretnie: miód = Żywność/miód, róże i kwiaty = Dom/kwiaty, chleb = Żywność/pieczywo.

Dozwolone kategorie:
{allowed_categories_prompt_text()}
'''

JSON_SCHEMA = {
    'name': 'receipt_ocr',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'merchant_name': {'type': 'string'},
            'purchased_at': {'type': ['string', 'null']},
            'total_amount': {'type': ['number', 'string', 'null']},
            'currency': {'type': 'string'},
            'payment_method': {'type': 'string'},
            'items': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
                'name': {'type': 'string'},
                'quantity': {'type': ['number', 'string', 'null']},
                'unit_price': {'type': ['number', 'string', 'null']},
                'paid_price': {'type': ['number', 'string', 'null']},
                'regular_price': {'type': ['number', 'string', 'null']},
                'discount_amount': {'type': ['number', 'string', 'null']},
                'promotion_name': {'type': 'string'},
                'is_discounted': {'type': 'boolean'},
                'category': {'type': 'string'},
                'subcategory': {'type': 'string'},
            }, 'required': ['name', 'quantity', 'unit_price', 'paid_price', 'regular_price', 'discount_amount', 'promotion_name', 'is_discounted', 'category', 'subcategory']}}
        },
        'required': ['merchant_name', 'purchased_at', 'total_amount', 'currency', 'payment_method', 'items']
    },
    'strict': True,
}


def _clean_item(item):
    name = item.get('name') or ''
    category, subcategory = infer_category_from_name(name, item.get('category'), item.get('subcategory'))
    return {
        'name': name,
        'quantity': _number(item.get('quantity')),
        'unit_price': _money(item.get('unit_price')),
        'paid_price': _money(item.get('paid_price')) or '0.00',
        'regular_price': _money(item.get('regular_price')),
        'discount_amount': _money(item.get('discount_amount')) or '0.00',
        'promotion_name': item.get('promotion_name') or '',
        'is_discounted': bool(item.get('is_discounted')),
        'category': category,
        'subcategory': subcategory,
    }


def _clean_response(data):
    items = data.get('items') or []
    if not isinstance(items, list):
        items = []
    return {
        'merchant_name': data.get('merchant_name') or '',
        'purchased_at': data.get('purchased_at'),
        'total_amount': _money(data.get('total_amount')),
        'currency': data.get('currency') or 'PLN',
        'payment_method': data.get('payment_method') or 'unknown',
        'items': [_clean_item(item) for item in items if isinstance(item, dict)],
    }


def parse_receipt_image(image_path: str) -> dict:
    client = OpenAI(api_key=settings.OPENAI_KEY)
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Przeanalizuj paragon i zwróć JSON zgodny ze schematem. Zachowaj polskie znaki i nie używaj kategorii Inne dla oczywistych produktów.'},
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}
            ]},
        ],
        response_format={'type': 'json_schema', 'json_schema': JSON_SCHEMA},
        temperature=0,
    )
    return _clean_response(json.loads(response.choices[0].message.content))
