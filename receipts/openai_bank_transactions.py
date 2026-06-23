import json
import re
from django.conf import settings
from openai import OpenAI
from .categories import allowed_bank_categories_prompt_text, normalize_bank_category
from .utils import normalize_text


SYSTEM_PROMPT = f'''
Jesteś klasyfikatorem polskich transakcji bankowych. Zwracasz wyłącznie poprawny JSON.
Klasyfikujesz pojedynczy wiersz wyciągu bankowego.

Zasady:
- Zachowuj polskie znaki.
- Kwota dodatnia to zwykle income, kwota ujemna to zwykle expense.
- Przelewy między własnymi kontami, zasilenia Revolut, pocket/kieszeń Revolut, oszczędności, spłaty własnej karty oraz wymiany walut oznaczaj jako internal_transfer.
- Revolut Exchange oraz opisy Exchanged to / Exchanged from to internal_transfer, nie wydatek.
- Zwroty od sprzedawców oznaczaj jako neutral z kategorią Promocje i korekty / zwrot.
- Przelew wewnętrzny nie jest wydatkiem ani przychodem budżetowym.
- category i subcategory muszą pochodzić dokładnie z poniższej listy.
- Nie używaj Inne/inne.

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


def looks_like_amount_fragment(value):
    value = str(value or '').strip()
    if not value:
        return True
    if len(value) <= 4 and re.fullmatch(r'-?[0-9,.]+', value):
        return True
    return bool(re.fullmatch(r'-?[0-9,.]+\s*(pln|eur|usd)?', value.lower()))


def looks_like_internal_transfer(tx):
    text = normalize_text(f'{tx.bank} {tx.merchant_name} {tx.raw_description} {tx.raw_row}')
    patterns = [
        'smart saver', 'konto oszcz', 'konto wlasne', 'wlasny rachunek', 'przelew wlasny',
        'transfer wewnetrzny', 'internal transfer', 'own account', 'between your accounts',
        'to your revolut', 'from your revolut', 'revolut top up', 'top up by bank card',
        'card top up', 'bank transfer to revolut', 'bank transfer from revolut',
        'zasilenie revolut', 'doladowanie revolut', 'wyplata z revolut', 'kałużny jacek', 'kaluzny jacek',
        'apple pay deposit', 'deposit by', 'depositing savings', 'to pocket', 'from pocket', 'pocket pln',
        'exchanged to', 'exchanged from', 'exchange current', 'wymiana walut', 'credit card repayment'
    ]
    return any(pattern in text for pattern in patterns)


def internal_subcategory(tx):
    text = normalize_text(f'{tx.bank} {tx.merchant_name} {tx.raw_description} {tx.raw_row}')
    if 'exchanged' in text or 'exchange' in text or 'wymiana walut' in text:
        return 'wymiana walut'
    if 'pocket' in text or 'kieszen' in text:
        return 'kieszeń Revolut'
    if 'saving' in text or 'oszcz' in text:
        return 'oszczędności'
    if 'credit card repayment' in text or 'karta kredytowa' in text:
        return 'karta kredytowa'
    if 'revolut' in text or 'apple pay deposit' in text or 'deposit by' in text:
        return 'Revolut'
    return 'konto własne'


def fallback_classification(tx):
    amount = tx.amount
    if looks_like_internal_transfer(tx):
        transaction_type = 'internal_transfer'
        category, subcategory = 'Przelewy wewnętrzne', internal_subcategory(tx)
    elif amount > 0:
        transaction_type = 'income'
        category, subcategory = 'Przychody', 'pozostałe przychody'
    elif amount < 0:
        transaction_type = 'expense'
        category, subcategory = 'Nieczytelne pozycje', 'produkt niejednoznaczny'
    else:
        transaction_type = 'neutral'
        category, subcategory = 'Promocje i korekty', 'korekta ceny'
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
    if looks_like_internal_transfer(tx):
        return fallback_classification(tx)
    if not getattr(settings, 'OPENAI_KEY', ''):
        return fallback_classification(tx)
    client = OpenAI(api_key=settings.OPENAI_KEY)
    payload = {'bank': tx.bank, 'booked_at': str(tx.booked_at or ''), 'transaction_at': str(tx.transaction_at or ''), 'merchant_name': tx.merchant_name, 'raw_description': tx.raw_description, 'amount': str(tx.amount), 'currency': tx.currency, 'raw_row': tx.raw_row}
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=[{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': 'Sklasyfikuj tę transakcję bankową:\n' + json.dumps(payload, ensure_ascii=False)}],
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
    if looks_like_amount_fragment(data.get('merchant_name')):
        data['merchant_name'] = tx.merchant_name or ''
    return data


def apply_bank_transaction_classification(tx):
    original_merchant_name = tx.merchant_name or ''
    data = classify_bank_transaction(tx)
    tx.corrected_description = data.get('corrected_description') or tx.raw_description or original_merchant_name
    tx.merchant_name = original_merchant_name
    tx.merchant_normalized = normalize_text(tx.merchant_name)
    tx.transaction_type = data.get('transaction_type') or ''
    tx.category = data.get('category') or ''
    tx.subcategory = data.get('subcategory') or ''
    tx.classification_source = 'openai' if data.get('notes') != 'fallback' else 'fallback'
    tx.raw_classification_json = data
    tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
    return tx
