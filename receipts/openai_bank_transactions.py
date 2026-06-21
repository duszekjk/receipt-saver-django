import json
from django.conf import settings
from openai import OpenAI
from .categories import allowed_bank_categories_prompt_text, normalize_bank_category
from .utils import normalize_text


SYSTEM_PROMPT = f'''
Jesteś klasyfikatorem polskich transakcji bankowych. Zwracasz wyłącznie poprawny JSON.
Klasyfikujesz pojedynczy wiersz wyciągu bankowego.

Ważne zasady:
- Jeśli kwota jest dodatnia, to zwykle transaction_type="income".
- Jeśli kwota jest ujemna, to zwykle transaction_type="expense".
- Jeśli opis wskazuje przelew między własnymi kontami, Smart Saver, konto oszczędnościowe, konto walutowe albo transfer wewnętrzny, użyj transaction_type="internal_transfer".
- Jeśli transakcja jest dopasowana do paragonu/faktury, jej kategoria bankowa jest tylko pomocnicza. Raport wydatków ma wtedy używać pozycji paragonu/faktury.
- Dla transakcji bez paragonu/faktury kategoria bankowa jest właściwą kategorią budżetową.
- Popraw błędy kodowania znaków, np. KA£UØNY -> KAŁUŻNY, POZNA— -> POZNAŃ, ålπski -> Śląski, ksiÍgowania -> księgowania.
- category i subcategory muszą być wybrane wyłącznie z listy.
- Nie wolno tworzyć własnych kategorii ani podkategorii.

Dozwolone kategorie:
{allowed_bank_categories_prompt_text()}
'''

JSON_SCHEMA = {
    'name': 'bank_transaction_classification',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'corrected_description': {'type': 'string'},
            'merchant_name': {'type': 'string'},
            'transaction_type': {'type': 'string', 'enum': ['expense', 'income', 'internal_transfer', 'neutral']},
            'category': {'type': 'string'},
            'subcategory': {'type': 'string'},
            'confidence': {'type': 'number'},
            'notes': {'type': 'string'},
        },
        'required': ['corrected_description', 'merchant_name', 'transaction_type', 'category', 'subcategory', 'confidence', 'notes'],
    },
    'strict': True,
}


def fallback_classification(tx):
    amount = tx.amount
    text = f'{tx.merchant_name} {tx.raw_description}'.lower()
    if 'smart saver' in text or 'konto oszcz' in text or 'konto direct' in text and tx.user.get_username().lower() in text:
        transaction_type = 'internal_transfer'
        category, subcategory = 'przelewy_wewnetrzne', 'konto_wlasne'
    elif amount > 0:
        transaction_type = 'income'
        category, subcategory = 'przychody', 'inne'
    elif amount < 0:
        transaction_type = 'expense'
        category, subcategory = 'inne', 'inne'
    else:
        transaction_type = 'neutral'
        category, subcategory = 'inne', 'inne'
    return {
        'corrected_description': tx.raw_description or tx.merchant_name or '',
        'merchant_name': tx.merchant_name or '',
        'transaction_type': transaction_type,
        'category': category,
        'subcategory': subcategory,
        'confidence': 0.25,
        'notes': 'fallback',
    }


def classify_bank_transaction(tx):
    if not getattr(settings, 'OPENAI_KEY', ''):
        return fallback_classification(tx)

    client = OpenAI(api_key=settings.OPENAI_KEY)
    payload = {
        'bank': tx.bank,
        'booked_at': str(tx.booked_at or ''),
        'transaction_at': str(tx.transaction_at or ''),
        'merchant_name': tx.merchant_name,
        'raw_description': tx.raw_description,
        'amount': str(tx.amount),
        'currency': tx.currency,
        'raw_row': tx.raw_row,
    }
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': 'Sklasyfikuj tę transakcję bankową:\n' + json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={'type': 'json_schema', 'json_schema': JSON_SCHEMA},
        temperature=0,
    )
    data = json.loads(response.choices[0].message.content)
    category, subcategory = normalize_bank_category(data.get('category'), data.get('subcategory'))
    data['category'] = category
    data['subcategory'] = subcategory
    if data.get('transaction_type') not in ['expense', 'income', 'internal_transfer', 'neutral']:
        data['transaction_type'] = fallback_classification(tx)['transaction_type']
    data['corrected_description'] = data.get('corrected_description') or tx.raw_description or tx.merchant_name or ''
    data['merchant_name'] = data.get('merchant_name') or tx.merchant_name or ''
    return data


def apply_bank_transaction_classification(tx):
    data = classify_bank_transaction(tx)
    tx.corrected_description = data.get('corrected_description') or ''
    tx.merchant_name = data.get('merchant_name') or tx.merchant_name
    tx.merchant_normalized = normalize_text(tx.merchant_name)
    tx.transaction_type = data.get('transaction_type') or ''
    tx.category = data.get('category') or ''
    tx.subcategory = data.get('subcategory') or ''
    tx.classification_source = 'openai' if data.get('notes') != 'fallback' else 'fallback'
    tx.raw_classification_json = data
    tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
    return tx
