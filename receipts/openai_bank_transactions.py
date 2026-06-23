import json
import re
from django.conf import settings
from openai import OpenAI
from .categories import allowed_bank_categories_prompt_text, normalize_bank_category
from .utils import normalize_text


class BankClassificationError(ValueError):
    pass


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
- Nie używaj ogólnych kategorii spoza listy, np. Zakupy. Wybierz najbliższą konkretną kategorię z listy.

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
        'zasilenie revolut', 'doladowanie revolut', 'wyplata z revolut', 'kaluzny jacek',
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


def deterministic_internal_transfer(tx):
    return {
        'corrected_description': tx.raw_description or tx.merchant_name or '',
        'merchant_name': tx.merchant_name or '',
        'transaction_type': 'internal_transfer',
        'category': 'Przelewy wewnętrzne',
        'subcategory': internal_subcategory(tx),
        'confidence': 1.0,
        'notes': 'deterministic_internal_transfer',
    }


def _payload(tx):
    return {
        'bank': tx.bank,
        'booked_at': str(tx.booked_at or ''),
        'transaction_at': str(tx.transaction_at or ''),
        'merchant_name': tx.merchant_name,
        'raw_description': tx.raw_description,
        'amount': str(tx.amount),
        'currency': tx.currency,
        'raw_row': tx.raw_row,
    }


def _chat_completion(client, messages):
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=messages,
        response_format={'type': 'json_schema', 'json_schema': JSON_SCHEMA},
        temperature=0,
    )
    return response.choices[0].message.content


def _clean_classification(raw_content, tx):
    data = json.loads(raw_content)
    category, subcategory = normalize_bank_category(data.get('category'), data.get('subcategory'))
    data['category'] = category
    data['subcategory'] = subcategory
    if data.get('transaction_type') not in ['expense', 'income', 'internal_transfer', 'neutral']:
        raise BankClassificationError(f'Niepoprawny transaction_type: {data.get("transaction_type")!r}')
    data['corrected_description'] = data.get('corrected_description') or tx.raw_description or tx.merchant_name or ''
    if looks_like_amount_fragment(data.get('merchant_name')):
        data['merchant_name'] = tx.merchant_name or ''
    return data


def classify_bank_transaction(tx):
    if looks_like_internal_transfer(tx):
        return deterministic_internal_transfer(tx)
    if not getattr(settings, 'OPENAI_KEY', ''):
        raise BankClassificationError('Brak OPENAI_KEY; nie można sklasyfikować transakcji bankowej.')

    client = OpenAI(api_key=settings.OPENAI_KEY)
    first_user_message = 'Sklasyfikuj tę transakcję bankową:\n' + json.dumps(_payload(tx), ensure_ascii=False)
    last_error = None

    for attempt in range(3):
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': first_user_message if attempt == 0 else first_user_message + f'\n\nTo jest ponowna próba numer {attempt + 1}. Poprzednie próby nie zwróciły poprawnej kategorii.'},
        ]
        for correction_index in range(10):
            raw_content = _chat_completion(client, messages)
            try:
                return _clean_classification(raw_content, tx)
            except (ValueError, json.JSONDecodeError, BankClassificationError) as error:
                last_error = error
                messages.append({'role': 'assistant', 'content': raw_content})
                messages.append({
                    'role': 'user',
                    'content': (
                        'Zwróciłeś niepoprawną klasyfikację transakcji bankowej. '
                        'Popraw cały JSON i odeślij ponownie wyłącznie poprawny JSON zgodny ze schematem.\n\n'
                        f'Błąd walidacji: {error}\n\n'
                        'category i subcategory muszą być dokładnie z listy dozwolonych kategorii. '
                        'Nie używaj kategorii spoza listy, takich jak Zakupy albo Inne. '
                        'Wybierz najbliższą konkretną kategorię z listy.'
                    )
                })
    raise BankClassificationError(f'Nie udało się sklasyfikować transakcji po 3 próbach i 10 korektach na próbę: {last_error}')


def apply_bank_transaction_classification(tx):
    original_merchant_name = tx.merchant_name or ''
    data = classify_bank_transaction(tx)
    tx.corrected_description = data.get('corrected_description') or tx.raw_description or original_merchant_name
    tx.merchant_name = original_merchant_name
    tx.merchant_normalized = normalize_text(tx.merchant_name)
    tx.transaction_type = data.get('transaction_type') or ''
    tx.category = data.get('category') or ''
    tx.subcategory = data.get('subcategory') or ''
    tx.classification_source = 'openai' if data.get('notes') != 'deterministic_internal_transfer' else 'deterministic'
    tx.raw_classification_json = data
    tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
    return tx
