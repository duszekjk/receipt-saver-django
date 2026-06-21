import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
import pandas as pd
from .utils import normalize_text


HEADER_MARKERS = ['data transakcji', 'data ksiegowania', 'kwota transakcji']


def parse_date(value):
    value = str(value or '').strip().strip('"')
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
    value = str(value or '0').strip().strip('"').replace('\xa0', '').replace(' ', '').replace(',', '.')
    value = ''.join(ch for ch in value if ch.isdigit() or ch in '.-')
    try:
        return Decimal(value or '0')
    except InvalidOperation:
        return Decimal('0')


def decode_text(raw):
    for encoding in ['utf-8-sig', 'cp1250', 'iso-8859-2', 'windows-1250', 'latin2']:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode('utf-8', errors='ignore')


def find_header_line(lines):
    for index, line in enumerate(lines):
        normalized = normalize_text(line)
        if all(marker in normalized for marker in HEADER_MARKERS):
            return index
        if 'data transakcji' in normalized and 'kwota' in normalized:
            return index
    return 0


def make_unique_headers(headers):
    result = []
    seen = {}
    for index, header in enumerate(headers):
        clean = str(header or '').strip().strip('"') or f'kolumna_{index}'
        if clean in seen:
            seen[clean] += 1
            clean = f'{clean}_{seen[clean]}'
        else:
            seen[clean] = 0
        result.append(clean)
    return result


def rows_from_records(records):
    rows = []
    for record in records:
        row = {str(k or '').strip(): '' if pd.isna(v) else v for k, v in record.items()}
        if any(str(v).strip() for v in row.values()):
            rows.append(row)
    return rows


def read_excel_rows(raw):
    frame = pd.read_excel(io.BytesIO(raw), dtype=str, header=None).fillna('')
    lines = [';'.join(str(value) for value in row) for row in frame.values.tolist()]
    header_index = find_header_line(lines)
    headers = make_unique_headers(frame.iloc[header_index].tolist())
    data = frame.iloc[header_index + 1:].copy()
    data.columns = headers
    return rows_from_records(data.to_dict(orient='records'))


def read_csv_rows(raw):
    text = decode_text(raw)
    lines = text.splitlines()
    header_index = find_header_line(lines)
    table_text = '\n'.join(lines[header_index:])
    try:
        dialect = csv.Sniffer().sniff(table_text[:4096], delimiters=';\t')
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ';'
    reader = csv.reader(io.StringIO(table_text), dialect=dialect)
    try:
        headers = make_unique_headers(next(reader))
    except StopIteration:
        return []
    rows = []
    for values in reader:
        if not any(str(value).strip() for value in values):
            continue
        if len(values) < len(headers):
            values = values + [''] * (len(headers) - len(values))
        row = dict(zip(headers, values[:len(headers)]))
        rows.append(row)
    return rows


def read_statement_rows(file_obj):
    name = getattr(file_obj, 'name', '').lower()
    raw = file_obj.read()
    if name.endswith(('.xls', '.xlsx')):
        return read_excel_rows(raw)
    return read_csv_rows(raw)


def exact_pick(row, *keys):
    normalized = {normalize_text(k): v for k, v in row.items()}
    for key in keys:
        value = normalized.get(normalize_text(key))
        if value not in (None, ''):
            return value
    return ''


def contains_pick(row, *keys):
    lower = {normalize_text(k): v for k, v in row.items()}
    for key in keys:
        normalized_key = normalize_text(key)
        for column, value in lower.items():
            if normalized_key == column or normalized_key in column:
                if value not in (None, ''):
                    return value
    return ''


def pick(row, *keys):
    return exact_pick(row, *keys) or contains_pick(row, *keys)


def clean_currency(value):
    value = str(value or '').strip().strip('"').upper()
    if re.fullmatch(r'[A-Z]{3}', value):
        return value
    return 'PLN'


def get_ing_amount_and_currency(row):
    amount = exact_pick(row, 'Kwota transakcji (waluta rachunku)')
    currency = exact_pick(row, 'Waluta')
    if not amount:
        amount = contains_pick(row, 'Kwota transakcji')
    return amount, clean_currency(currency)


def parse_bank_statement(file_obj, bank: str):
    for row in read_statement_rows(file_obj):
        booked = pick(row, 'Data księgowania', 'Data ksiegowania')
        tx_date = pick(row, 'Data transakcji', 'Data operacji', 'Data')
        desc = ' '.join(
            part.strip()
            for part in [
                str(pick(row, 'Dane kontrahenta', 'Kontrahent')).strip(),
                str(pick(row, 'Tytuł', 'Tytul', 'Opis transakcji', 'Opis')).strip(),
                str(pick(row, 'Szczegóły', 'Szczegoly')).strip(),
            ]
            if part
        )
        if bank == 'ing':
            amount, currency = get_ing_amount_and_currency(row)
        else:
            amount = pick(row, 'Kwota transakcji', 'Kwota', 'Obciążenia', 'Obciazenia', 'Uznania')
            currency = clean_currency(exact_pick(row, 'Waluta') or 'PLN')

        parsed_amount = parse_amount(amount)
        parsed_date = parse_date(tx_date) or parse_date(booked)
        if not parsed_date and parsed_amount == 0 and not desc:
            continue
        if not parsed_date or parsed_amount == 0:
            continue

        yield {
            'booked_at': parse_date(booked) or parsed_date,
            'transaction_at': parsed_date,
            'merchant_name': desc[:255] or 'Nieznany kontrahent',
            'merchant_normalized': normalize_text(desc),
            'raw_description': desc,
            'amount': parsed_amount,
            'currency': currency,
            'raw_row': row,
        }


def parse_bank_csv(file_obj, bank: str):
    return parse_bank_statement(file_obj, bank)
