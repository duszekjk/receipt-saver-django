import base64
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from openai import OpenAI

from .categories import allowed_categories_prompt_text, normalize_category


class ReceiptParseError(ValueError):
    pass


class ReceiptUnreadableError(ReceiptParseError):
    pass


class ReceiptDateUnreadableError(ReceiptParseError):
    pass


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


def _code(value):
    value = ''.join(ch for ch in str(value or '').strip() if ch.isalnum())
    return value[:128]


SYSTEM_PROMPT = f'''
Jesteś parserem polskich paragonów i ekspertem od klasyfikacji produktów z polskich sklepów.
Zwracasz wyłącznie poprawny JSON zgodny ze schematem. Nie dodajesz komentarzy poza JSON.

Najważniejsza zasada: jeden obraz paragonu opisuje jeden paragon, a cały paragon ma dokładnie jedną datę i godzinę zakupu w polu purchased_at. Pozycje produktów NIGDY nie mają własnych dat. Nie interpretuj numerów produktów, kodów, NIP, numerów terminala, numerów karty ani lat widocznych w innych miejscach jako dat pozycji.

Twoje zadania:
1. Oceń, czy obraz jest wystarczająco czytelny do wiarygodnego odczytania pozycji paragonu. Jeśli nie, ustaw scan_status na unreadable_receipt i krótko opisz problem w scan_error.
2. Odczytaj sklep, jedną datę i godzinę całego paragonu, sumę, walutę, metodę płatności oraz — jeśli jest widoczny — identyfikator karty, najlepiej ostatnie 4 cyfry w payment_card_last4.
3. Odczytaj kod kreskowy / numer systemowy paragonu do receipt_barcode, jeśli jest widoczny i jednoznaczny. To ma być kod identyfikujący cały paragon, a nie NIP sklepu, numer terminala, numer karty ani numer produktu. Jeśli nie masz pewności, zwróć pusty string.
4. Odczytaj wszystkie pozycje paragonu.
5. Dla każdej pozycji wybierz category i subcategory wyłącznie z listy dozwolonych kategorii poniżej.
6. Zachowaj i odtwarzaj polskie znaki w nazwach produktów, kategorii i podkategorii.

Zasady daty:
- purchased_at jest jedyną datą całego paragonu. Wszystkie produkty należą do tej jednej daty.
- Nie zgaduj daty. Jeśli produkty i kwoty są czytelne, ale data paragonu nie jest jednoznacznie czytelna, ustaw scan_status na unreadable_date, purchased_at na null i wyjaśnij problem w scan_error.
- Zwykle skanowane są świeże paragony. Data starsza niż 12 miesięcy względem dzisiejszej daty jest skrajnie podejrzana i nie wolno jej zwracać bez bardzo wyraźnego odczytu z właściwego pola daty paragonu.
- Data z przyszłości jest niepoprawna.
- Nie używaj przypadkowych liczb typu 2022, 2023 itd. znalezionych w kodzie produktu, stopce, numerze dokumentu lub danych terminala jako daty zakupu.
- Jeśli data wygląda na starszą niż 12 miesięcy albo przyszłą, a nie jest absolutnie jednoznaczna, zwróć unreadable_date zamiast wymyślać datę.

Bardzo ważne zasady dotyczące polskich znaków:
- Jeżeli na paragonie, zdjęciu albo OCR nie pokazuje polskich znaków, dodaj je z kontekstu języka polskiego.
- "zelatyna" zwróć jako "żelatyna". "maka" jako "mąka", "smietana" jako "śmietana", "wedlina" jako "wędlina", "ogorek" jako "ogórek", "jablko" jako "jabłko", "miod" jako "miód", "roze" jako "róże", gdy wynika to z kontekstu.
- Nie usuwaj polskich znaków.

Zasady klasyfikacji:
- Każdy produkt musi mieć category i subcategory.
- category i subcategory muszą być wybrane wyłącznie z listy dozwolonych kategorii i dokładnie tak zapisane jak na liście.
- Nie twórz własnych kategorii ani podkategorii. Nie istnieje kategoria "Inne" ani podkategoria "inne".
- Jeżeli produkt jest trudny, techniczny albo pomocniczy, wybierz najlepszą konkretną kategorię z listy.
- Jeżeli produkt jest żywnością, wybierz najbliższą podkategorię w Żywność.
- Nie klasyfikuj całego paragonu według sklepu. Klasyfikuj każdy produkt osobno.

Przykłady: jaja -> Żywność / jaja; żelatyna -> Żywność / dodatki do pieczenia; mąka -> Żywność / produkty sypkie; miód -> Żywność / miód; róże -> Dom / kwiaty; reklamówka -> Opakowania i torby / reklamówki.

Dozwolone kategorie i podkategorie — nie wolno pominąć tej listy i nie wolno tworzyć nic poza nią:
{allowed_categories_prompt_text()}
'''

JSON_SCHEMA = {
    'name': 'receipt_ocr',
    'schema': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'scan_status': {'type': 'string', 'enum': ['ok', 'unreadable_receipt', 'unreadable_date']},
            'scan_error': {'type': 'string'},
            'merchant_name': {'type': 'string'},
            'receipt_barcode': {'type': 'string'},
            'purchased_at': {'type': ['string', 'null']},
            'total_amount': {'type': ['number', 'string', 'null']},
            'currency': {'type': 'string'},
            'payment_method': {'type': 'string'},
            'payment_card_last4': {'type': ['string', 'null']},
            'items': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
                'name': {'type': 'string'}, 'quantity': {'type': ['number', 'string', 'null']}, 'unit_price': {'type': ['number', 'string', 'null']},
                'paid_price': {'type': ['number', 'string', 'null']}, 'regular_price': {'type': ['number', 'string', 'null']},
                'discount_amount': {'type': ['number', 'string', 'null']}, 'promotion_name': {'type': 'string'}, 'is_discounted': {'type': 'boolean'},
                'category': {'type': 'string'}, 'subcategory': {'type': 'string'},
            }, 'required': ['name', 'quantity', 'unit_price', 'paid_price', 'regular_price', 'discount_amount', 'promotion_name', 'is_discounted', 'category', 'subcategory']}}
        },
        'required': ['scan_status', 'scan_error', 'merchant_name', 'receipt_barcode', 'purchased_at', 'total_amount', 'currency', 'payment_method', 'payment_card_last4', 'items']
    }, 'strict': True,
}


def _clean_item(item):
    name = (item.get('name') or '').strip()
    if not name:
        raise ReceiptParseError('Pozycja paragonu bez nazwy produktu.')
    category, subcategory = normalize_category(item.get('category'), item.get('subcategory'))
    return {'name': name, 'quantity': _number(item.get('quantity')), 'unit_price': _money(item.get('unit_price')), 'paid_price': _money(item.get('paid_price')) or '0.00', 'regular_price': _money(item.get('regular_price')), 'discount_amount': _money(item.get('discount_amount')) or '0.00', 'promotion_name': item.get('promotion_name') or '', 'is_discounted': bool(item.get('is_discounted')), 'category': category, 'subcategory': subcategory}


def _validate_date(value):
    if not value:
        raise ReceiptDateUnreadableError('Data paragonu jest nieczytelna. Wpisz datę zakupu ręcznie.')
    purchased_at = parse_datetime(value)
    if not purchased_at:
        raise ReceiptDateUnreadableError('Nie udało się jednoznacznie odczytać daty paragonu. Wpisz datę zakupu ręcznie.')
    if timezone.is_naive(purchased_at):
        purchased_at = timezone.make_aware(purchased_at, timezone.get_current_timezone())
    now = timezone.now()
    if purchased_at > now + timedelta(days=1) or purchased_at < now - timedelta(days=366):
        raise ReceiptDateUnreadableError('Nie udało się wiarygodnie odczytać daty paragonu. Wpisz datę zakupu ręcznie.')
    return purchased_at.isoformat()


def _clean_response(data):
    status = data.get('scan_status')
    error = (data.get('scan_error') or '').strip()
    if status == 'unreadable_receipt':
        raise ReceiptUnreadableError(error or 'Paragon jest nieczytelny. Zrób wyraźniejsze zdjęcie i spróbuj ponownie.')
    items = data.get('items') or []
    if not isinstance(items, list) or not items:
        raise ReceiptUnreadableError('Nie udało się wiarygodnie odczytać pozycji paragonu. Zrób wyraźniejsze zdjęcie.')
    cleaned = {'scan_status': status or 'ok', 'scan_error': error, 'merchant_name': data.get('merchant_name') or '', 'receipt_barcode': _code(data.get('receipt_barcode')), 'purchased_at': None, 'total_amount': _money(data.get('total_amount')), 'currency': data.get('currency') or 'PLN', 'payment_method': data.get('payment_method') or 'unknown', 'payment_card_last4': data.get('payment_card_last4'), 'items': [_clean_item(item) for item in items if isinstance(item, dict)]}
    if status == 'unreadable_date':
        return cleaned
    try:
        cleaned['purchased_at'] = _validate_date(data.get('purchased_at'))
    except ReceiptDateUnreadableError as error:
        cleaned['scan_status'] = 'unreadable_date'
        cleaned['scan_error'] = str(error)
    return cleaned


def _call_openai(client, b64, extra_instruction=''):
    user_text = 'Przeanalizuj jeden cały paragon. Ustal jedną datę dla całego paragonu; produkty nie mają osobnych dat. Nie zgaduj daty ani nie bierz roku z kodów lub stopki. Jeśli obraz jest nieczytelny zwróć unreadable_receipt. Jeśli pozycje są czytelne, ale data nie, zwróć unreadable_date. Odczytaj końcówkę karty oraz kod kreskowy/numer systemowy paragonu, jeśli są widoczne. Dodaj polskie znaki z kontekstu. Każda pozycja musi mieć kategorię i podkategorię z listy.'
    if extra_instruction:
        user_text += '\n\nPoprzedni wynik był niepoprawny. Popraw wynik na podstawie tego samego obrazu, nie zgaduj brakujących danych. Błąd walidacji: ' + extra_instruction
    response = client.chat.completions.create(model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'), messages=[{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': [{'type': 'text', 'text': user_text}, {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}]}], response_format={'type': 'json_schema', 'json_schema': JSON_SCHEMA}, temperature=0)
    return json.loads(response.choices[0].message.content)


def parse_receipt_image(image_path: str) -> dict:
    client = OpenAI(api_key=settings.OPENAI_KEY)
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    last_error = None
    for attempt in range(3):
        try:
            data = _call_openai(client, b64, str(last_error) if last_error else '')
            return _clean_response(data)
        except ReceiptUnreadableError:
            raise
        except (ValueError, ReceiptParseError, json.JSONDecodeError) as error:
            last_error = error
    raise ReceiptParseError(f'Nie udało się poprawnie zdekodować paragonu po 3 próbach: {last_error}')
