import re
import unicodedata
from decimal import Decimal
from rapidfuzz import fuzz


def normalize_text(value: str) -> str:
    value = value or ''
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r'[^a-z0-9ąćęłńóśżź ]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    for junk in ['sp z o o', 's a', 'sklep', 'market']:
        value = value.replace(junk, ' ')
    return re.sub(r'\s+', ' ', value).strip()


def money_similarity(a, b, tolerance=Decimal('0.50')) -> float:
    if a is None or b is None:
        return 0.0
    diff = abs(Decimal(a) - Decimal(b))
    if diff <= tolerance:
        return 1.0
    if diff > Decimal('10.00'):
        return 0.0
    return max(0.0, 1.0 - float(diff / Decimal('10.00')))


def text_similarity(a: str, b: str) -> float:
    a, b = normalize_text(a), normalize_text(b)
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def build_receipt_fingerprint(data: dict) -> str:
    merchant = normalize_text(data.get('merchant_name', ''))
    purchased_at = (data.get('purchased_at') or '')[:10]
    total = str(data.get('total_amount') or '')
    items = data.get('items', []) or []
    names = sorted(normalize_text(i.get('name', '')) for i in items)[:8]
    return '|'.join([merchant, purchased_at, total, str(len(items)), ','.join(names)])
