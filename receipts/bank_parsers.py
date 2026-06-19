import csv
import io
from datetime import datetime
from decimal import Decimal
import pandas as pd
from .utils import normalize_text


def parse_date(value):
    value = str(value or '').strip()
    if not value or value.lower() in {'nan', 'nat'}:
        return None
    if hasattr(value, 'date'):
        return value.date()
    for fmt in ['%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y']:
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            pass
    try:
        return pd.to_datetime(value, dayfirst=True).date()
    except Exception:
        return None


def parse_amount(value):
    value = str(value or '0').replace('\xa0', '').replace(' ', '').replace(',', '.')
    value = ''.join(ch for ch in value if ch.isdigit() or ch in '.-')
    return Decimal(value or '0')


def read_statement_rows(file_obj):
    name = getattr(file_obj, 'name', '').lower()
    raw = file_obj.read()
    if name.endswith(('.xls', '.xlsx')):
        frame = pd.read_excel(io.BytesIO(raw), dtype=str)
        return frame.fillna('').to_dict(orient='records')

    for encoding in ['utf-8-sig', 'cp1250', 'iso-8859-2']:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            text = raw.decode('utf-8', errors='ignore')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=';,\t')
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ';'
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def pick(row, *keys):
    lower = {normalize_text(k): v for k, v in row.items()}
    for key in keys:
        value = lower.get(normalize_text(key))
        if value not in (None, ''):
            return value
    return ''


def parse_bank_statement(file_obj, bank: str):
    for row in read_statement_rows(file_obj):
        if bank == 'ing':
            booked = pick(row, 'Data księgowania', 'Data ksiegowania', 'Data transakcji')
            tx_date = pick(row, 'Data transakcji', 'Data księgowania', 'Data ksiegowania')
            desc = pick(row, 'Dane kontrahenta', 'Tytuł', 'Tytul', 'Opis transakcji', 'Opis')
            amount = pick(row, 'Kwota transakcji', 'Kwota', 'Kwota w walucie rachunku')
        elif bank == 'santander':
            booked = pick(row, 'Data księgowania', 'Data ksiegowania', 'Data')
            tx_date = pick(row, 'Data transakcji', 'Data operacji', 'Data')
            desc = pick(row, 'Opis transakcji', 'Kontrahent', 'Tytuł', 'Tytul', 'Opis')
            amount = pick(row, 'Kwota', 'Kwota transakcji', 'Obciążenia', 'Obciazenia')
        else:
            booked = pick(row, 'Data księgowania', 'Data ksiegowania', 'Data')
            tx_date = pick(row, 'Data transakcji', 'Data')
            desc = pick(row, 'Opis', 'Tytuł', 'Tytul', 'Opis transakcji')
            amount = pick(row, 'Kwota', 'Kwota transakcji')

        parsed_amount = parse_amount(amount)
        if not booked and not tx_date and parsed_amount == 0 and not desc:
            continue
        yield {
            'booked_at': parse_date(booked),
            'transaction_at': parse_date(tx_date),
            'merchant_name': str(desc)[:255],
            'merchant_normalized': normalize_text(desc),
            'raw_description': str(desc),
            'amount': parsed_amount,
            'currency': 'PLN',
            'raw_row': row,
        }


def parse_bank_csv(file_obj, bank: str):
    return parse_bank_statement(file_obj, bank)
