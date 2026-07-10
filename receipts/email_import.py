import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from html import unescape
from io import BytesIO

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from openai import OpenAI

from .categories import normalize_bank_category
from .models import BankTransaction
from .utils import normalize_text


class EmailImportError(ValueError):
    pass


EMAIL_JSON_SCHEMA = {
    'name': 'purchase_email_import',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'merchant_name': {'type': 'string'},
            'purchase_description': {'type': 'string'},
            'purchased_at': {'type': ['string', 'null']},
            'amount': {'type': ['number', 'string', 'null']},
            'currency': {'type': 'string'},
            'category': {'type': 'string'},
            'subcategory': {'type': 'string'},
            'confidence': {'type': 'number'},
        },
        'required': ['merchant_name', 'purchase_description', 'purchased_at', 'amount', 'currency', 'category', 'subcategory', 'confidence'],
    },
    'strict': True,
}


def _strip_html(value):
    value = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', value or '')
    value = re.sub(r'(?s)<[^>]+>', ' ', value)
    return re.sub(r'\s+', ' ', unescape(value)).strip()


def _decode_text(data):
    for encoding in ('utf-8', 'utf-16', 'windows-1250', 'iso-8859-2'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _extract_pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        return ''
    try:
        reader = PdfReader(BytesIO(data))
        return '\n'.join(page.extract_text() or '' for page in reader.pages)
    except Exception:
        return ''


def extract_attachment_text(upload):
    data = upload.read()
    upload.seek(0)
    content_type = (getattr(upload, 'content_type', '') or '').lower()
    name = (getattr(upload, 'name', '') or '').lower()
    if content_type == 'application/pdf' or name.endswith('.pdf'):
        return _extract_pdf_text(data)
    if content_type.startswith('text/') or name.endswith(('.txt', '.html', '.htm', '.csv', '.xml')):
        text = _decode_text(data)
        return _strip_html(text) if 'html' in content_type or name.endswith(('.html', '.htm')) else text
    return ''


def _money(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace(',', '.')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def analyze_purchase_email(text, attachment_text=''):
    if not getattr(settings, 'OPENAI_KEY', ''):
        raise EmailImportError('Brak OPENAI_KEY.')
    combined = '\n\n'.join(part.strip() for part in [text or '', attachment_text or ''] if part and part.strip())
    if not combined:
        raise EmailImportError('Brak treści wiadomości lub czytelnego załącznika.')
    client = OpenAI(api_key=settings.OPENAI_KEY)
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=[
            {
                'role': 'system',
                'content': (
                    'Analizujesz wiadomość e-mail będącą potwierdzeniem zakupu, rachunkiem albo fakturą. '
                    'Wyodrębnij faktycznie kupioną usługę lub produkt, datę, kwotę i walutę. '
                    'Nie klasyfikuj wszystkich zakupów Apple jako akcesoria komputerowe. '
                    'iCloud to usługi cyfrowe/subskrypcje, filmy to rozrywka, aplikacje to aplikacje i usługi cyfrowe. '
                    'Nie zgaduj danych, których nie ma. Category i subcategory muszą należeć do kategorii bankowych aplikacji.'
                ),
            },
            {'role': 'user', 'content': combined[:50000]},
        ],
        response_format={'type': 'json_schema', 'json_schema': EMAIL_JSON_SCHEMA},
        temperature=0,
        timeout=60,
    )
    data = json.loads(response.choices[0].message.content)
    category, subcategory = normalize_bank_category(data.get('category'), data.get('subcategory'))
    data['category'] = category
    data['subcategory'] = subcategory
    data['amount'] = _money(data.get('amount'))
    return data


def _purchase_date(value):
    if not value:
        return None
    parsed_datetime = parse_datetime(value)
    if parsed_datetime:
        return parsed_datetime.date()
    return parse_date(value)


def _visible_transactions(user):
    profile = getattr(user, 'receipt_profile', None)
    family = profile.family if profile and profile.family_id else None
    if user.is_superuser:
        return BankTransaction.objects.all()
    return BankTransaction.objects.filter(family=family) if family else BankTransaction.objects.filter(user=user)


def find_matching_transaction(user, analysis):
    amount = analysis.get('amount')
    purchase_date = _purchase_date(analysis.get('purchased_at'))
    if amount is None or purchase_date is None:
        return None
    qs = _visible_transactions(user).filter(amount__lt=0)
    qs = qs.filter(amount__gte=-(amount + Decimal('0.01')), amount__lte=-(amount - Decimal('0.01')))
    qs = qs.filter(transaction_at__range=[purchase_date - timedelta(days=2), purchase_date + timedelta(days=2)]) | qs.filter(
        booked_at__range=[purchase_date - timedelta(days=2), purchase_date + timedelta(days=2)]
    )
    merchant = normalize_text(analysis.get('merchant_name') or '')
    candidates = list(qs.distinct().order_by('-transaction_at', '-booked_at')[:20])
    if merchant:
        candidates.sort(key=lambda tx: merchant not in normalize_text(f'{tx.merchant_name} {tx.raw_description} {tx.corrected_description}'))
    return candidates[0] if candidates else None


def apply_email_analysis(user, analysis, source_text=''):
    tx = find_matching_transaction(user, analysis)
    if not tx:
        return None
    with transaction.atomic():
        tx.corrected_description = analysis.get('purchase_description') or tx.corrected_description or tx.raw_description
        tx.category = analysis.get('category') or tx.category
        tx.subcategory = analysis.get('subcategory') or tx.subcategory
        tx.classification_source = 'email'
        payload = dict(tx.raw_classification_json or {})
        payload['email_import'] = {
            'merchant_name': analysis.get('merchant_name') or '',
            'purchase_description': analysis.get('purchase_description') or '',
            'purchased_at': analysis.get('purchased_at'),
            'amount': str(analysis.get('amount') or ''),
            'currency': analysis.get('currency') or '',
            'confidence': analysis.get('confidence') or 0,
            'source_excerpt': (source_text or '')[:2000],
            'imported_at': timezone.now().isoformat(),
        }
        tx.raw_classification_json = payload
        tx.save(update_fields=['corrected_description', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
    return tx
