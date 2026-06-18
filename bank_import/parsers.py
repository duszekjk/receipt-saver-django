import csv
import io
from datetime import datetime
from decimal import Decimal
from receipts.utils import normalize_text


def parse_date(value):
    value = (value or '').strip()
    for fmt in ['%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(value):
    value = str(value).replace(' ', '').replace(',', '.')
    value = ''.join(ch for ch in value if ch.isdigit() or ch in '.-')
    return Decimal(value or '0')


def parse_bank_csv(file_obj, bank: str):
    text = file_obj.read().decode('utf-8-sig', errors='ignore')
    dialect = csv.Sniffer().sniff(text[:2048], delimiters=';,')
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    for row in reader:
        lower = {normalize_text(k): v for k, v in row.items()}
        if bank == 'ing':
            booked = lower.get('data ksiegowania') or lower.get('data transakcji')
            tx_date = lower.get('data transakcji') or booked
            desc = lower.get('dane kontrahenta') or lower.get('tytul') or lower.get('opis transakcji') or ''
            amount = lower.get('kwota transakcji') or lower.get('kwota') or '0'
        elif bank == 'santander':
            booked = lower.get('data ksiegowania') or lower.get('data')
            tx_date = lower.get('data transakcji') or booked
            desc = lower.get('opis transakcji') or lower.get('kontrahent') or lower.get('tytul') or ''
            amount = lower.get('kwota') or lower.get('kwota transakcji') or '0'
        else:
            booked = lower.get('data ksiegowania') or lower.get('data')
            tx_date = lower.get('data transakcji') or booked
            desc = lower.get('opis') or lower.get('tytul') or ''
            amount = lower.get('kwota') or '0'
        yield {
            'booked_at': parse_date(booked),
            'transaction_at': parse_date(tx_date),
            'merchant_name': desc[:255],
            'merchant_normalized': normalize_text(desc),
            'raw_description': desc,
            'amount': parse_amount(amount),
            'currency': 'PLN',
            'raw_row': row,
        }
