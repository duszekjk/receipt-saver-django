import json
import re
import time
from django.conf import settings
from openai import OpenAI
from .categories import allowed_bank_categories_prompt_text, normalize_bank_category
from .utils import normalize_text


class BankClassificationError(ValueError):
    pass


SYSTEM_PROMPT = f'''
Jesteś klasyfikatorem polskich transakcji bankowych. Klasyfikujesz CAŁY importowany wyciąg bankowy w jednej odpowiedzi.
Zwracasz wyłącznie poprawny JSON zgodny ze schematem.

Zasady:
- Zachowuj polskie znaki.
- Każda transakcja z wejścia musi mieć dokładnie jedną klasyfikację w odpowiedzi.
- Nie pomijaj żadnej transakcji i nie dodawaj transakcji spoza wejścia.
- Pole index w odpowiedzi musi odpowiadać indexowi transakcji z wejścia.
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

STATEMENT_JSON_SCHEMA = {
    'name': 'bank_statement_classification',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'transactions': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'index': {'type': 'integer'},
                        'corrected_description': {'type': 'string'},
                        'merchant_name': {'type': 'string'},
                        'transaction_type': {'type': 'string', 'enum': ['expense', 'income', 'internal_transfer', 'neutral']},
                        'category': {'type': 'string'},
                        'subcategory': {'type': 'string'},
                        'confidence': {'type': 'number'},
                        'notes': {'type': 'string'},
                    },
                    'required': ['index', 'corrected_description', 'merchant_name', 'transaction_type', 'category', 'subcategory', 'confidence', 'notes'],
                },
            }
        },
        'required': ['transactions'],
    },
    'strict': True,
}

SINGLE_JSON_SCHEMA = {
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

BANK_CLASSIFICATION_OVERALL_TIMEOUT_SECONDS = 45
OPENAI_REQUEST_TIMEOUT_SECONDS = 25


def looks_like_amount_fragment(value):
    value = str(value or '').strip()
    if not value:
        return True
    if len(value) <= 4 and re.fullmatch(r'-?[0-9,.]+', value):
        return True
    return bool(re.fullmatch(r'-?[0-9,.]+\s*(pln|eur|usd)?', value.lower()))


def _normalize_text_from_row(bank, row):
    return normalize_text(f'{bank} {row.get("merchant_name", "")} {row.get("raw_description", "")} {row.get("raw_row", {})}')


def _statement_payload(bank, rows):
    return {
        'bank': bank,
        'transactions': [
            {
                'index': index,
                'booked_at': str(row.get('booked_at') or ''),
                'transaction_at': str(row.get('transaction_at') or ''),
                'merchant_name': row.get('merchant_name') or '',
                'raw_description': row.get('raw_description') or '',
                'amount': str(row.get('amount') or ''),
                'currency': row.get('currency') or '',
                'raw_row': row.get('raw_row') or {},
            }
            for index, row in enumerate(rows)
        ],
    }


def _chat_completion(client, messages, schema):
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=messages,
        response_format={'type': 'json_schema', 'json_schema': schema},
        temperature=0,
        timeout=OPENAI_REQUEST_TIMEOUT_SECONDS,
    )
    return response.choices[0].message.content


def _validate_statement_response(raw_content, bank, rows):
    data = json.loads(raw_content)
    transactions = data.get('transactions')
    if not isinstance(transactions, list):
        raise BankClassificationError('Pole transactions nie jest listą.')
    if len(transactions) != len(rows):
        raise BankClassificationError(f'Liczba klasyfikacji ({len(transactions)}) nie zgadza się z liczbą transakcji ({len(rows)}).')

    expected_indexes = set(range(len(rows)))
    seen_indexes = set()
    cleaned_by_index = {}
    errors = []

    for item in transactions:
        index = item.get('index')
        if index not in expected_indexes:
            errors.append(f'Niepoprawny index: {index!r}.')
            continue
        if index in seen_indexes:
            errors.append(f'Powtórzony index: {index}.')
            continue
        seen_indexes.add(index)
        try:
            category, subcategory = normalize_bank_category(item.get('category'), item.get('subcategory'))
        except ValueError as error:
            errors.append(f'Index {index}: {error}')
            continue
        transaction_type = item.get('transaction_type')
        if transaction_type not in ['expense', 'income', 'internal_transfer', 'neutral']:
            errors.append(f'Index {index}: niepoprawny transaction_type {transaction_type!r}.')
            continue
        source_row = rows[index]
        merchant_name = item.get('merchant_name') or source_row.get('merchant_name') or ''
        if looks_like_amount_fragment(merchant_name):
            merchant_name = source_row.get('merchant_name') or ''
        cleaned_by_index[index] = {
            'corrected_description': item.get('corrected_description') or source_row.get('raw_description') or source_row.get('merchant_name') or '',
            'merchant_name': merchant_name,
            'transaction_type': transaction_type,
            'category': category,
            'subcategory': subcategory,
            'confidence': item.get('confidence') or 0,
            'notes': item.get('notes') or '',
        }

    missing = expected_indexes - seen_indexes
    if missing:
        errors.append('Brak klasyfikacji dla indexów: ' + ', '.join(str(index) for index in sorted(missing)) + '.')
    if errors:
        raise BankClassificationError('\n'.join(errors))
    return [cleaned_by_index[index] for index in range(len(rows))]


def classify_bank_statement_rows(bank, rows):
    rows = list(rows)
    if not rows:
        return []
    if not getattr(settings, 'OPENAI_KEY', ''):
        raise BankClassificationError('Brak OPENAI_KEY; nie można sklasyfikować wyciągu bankowego.')

    started_at = time.monotonic()
    client = OpenAI(api_key=settings.OPENAI_KEY, timeout=OPENAI_REQUEST_TIMEOUT_SECONDS)
    payload = _statement_payload(bank, rows)
    first_user_message = 'Sklasyfikuj cały importowany wyciąg bankowy w jednej odpowiedzi:\n' + json.dumps(payload, ensure_ascii=False)
    last_error = None

    for attempt in range(3):
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': first_user_message if attempt == 0 else first_user_message + f'\n\nTo jest pełna ponowna próba numer {attempt + 1}. Poprzednia rozmowa nie doprowadziła do poprawnej klasyfikacji całego wyciągu.'},
        ]
        for _ in range(10):
            if time.monotonic() - started_at > BANK_CLASSIFICATION_OVERALL_TIMEOUT_SECONDS:
                raise BankClassificationError('Klasyfikacja wyciągu trwała zbyt długo. Spróbuj ponownie albo zaimportuj krótszy zakres transakcji.')
            try:
                raw_content = _chat_completion(client, messages, STATEMENT_JSON_SCHEMA)
                return _validate_statement_response(raw_content, bank, rows)
            except (ValueError, json.JSONDecodeError, BankClassificationError) as error:
                last_error = error
                messages.append({'role': 'assistant', 'content': locals().get('raw_content', '')})
                messages.append({
                    'role': 'user',
                    'content': (
                        'Zwróciłeś niepoprawną klasyfikację całego wyciągu bankowego. '
                        'Popraw cały JSON i odeślij ponownie wyłącznie poprawny JSON zgodny ze schematem.\n\n'
                        f'Błędy walidacji:\n{error}\n\n'
                        'Każda transakcja z wejścia musi mieć dokładnie jedną klasyfikację. '
                        'Indexy muszą odpowiadać wejściu. category i subcategory muszą być dokładnie z listy dozwolonych kategorii. '
                        'Nie używaj kategorii spoza listy, takich jak Zakupy albo Inne.'
                    )
                })
            except Exception as error:
                raise BankClassificationError(f'Błąd komunikacji z OpenAI podczas klasyfikacji wyciągu: {error}')
    raise BankClassificationError(f'Nie udało się sklasyfikować całego wyciągu po 3 próbach i 10 korektach na próbę: {last_error}')


def classify_bank_transaction(tx):
    row = {
        'booked_at': tx.booked_at,
        'transaction_at': tx.transaction_at,
        'merchant_name': tx.merchant_name,
        'raw_description': tx.raw_description,
        'amount': tx.amount,
        'currency': tx.currency,
        'raw_row': tx.raw_row,
    }
    return classify_bank_statement_rows(tx.bank, [row])[0]


def apply_bank_transaction_classification(tx):
    original_merchant_name = tx.merchant_name or ''
    data = classify_bank_transaction(tx)
    tx.corrected_description = data.get('corrected_description') or tx.raw_description or original_merchant_name
    tx.merchant_name = original_merchant_name
    tx.merchant_normalized = normalize_text(tx.merchant_name)
    tx.transaction_type = data.get('transaction_type') or ''
    tx.category = data.get('category') or ''
    tx.subcategory = data.get('subcategory') or ''
    tx.classification_source = 'openai'
    tx.raw_classification_json = data
    tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
    return tx
