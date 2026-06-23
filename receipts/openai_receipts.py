import base64
import json
from decimal import Decimal, InvalidOperation
from django.conf import settings
from openai import OpenAI
from .categories import allowed_categories_prompt_text, normalize_category


class ReceiptParseError(ValueError):
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


SYSTEM_PROMPT = f'''
Jesteś parserem polskich paragonów i ekspertem od klasyfikacji produktów z polskich sklepów.
Zwracasz wyłącznie poprawny JSON zgodny ze schematem. Nie dodajesz komentarzy poza JSON.

Twoje zadania:
1. Odczytaj z paragonu sklep, datę, godzinę, sumę, walutę i metodę płatności.
2. Odczytaj wszystkie pozycje paragonu: nazwę produktu, ilość, cenę jednostkową, cenę zapłaconą, cenę regularną, rabat, nazwę promocji i informację, czy produkt był przeceniony.
3. Dla każdej pozycji wybierz category i subcategory wyłącznie z listy dozwolonych kategorii poniżej.
4. Zachowaj i odtwarzaj polskie znaki w nazwach produktów, kategorii i podkategorii.

Bardzo ważne zasady dotyczące polskich znaków:
- Jeżeli na paragonie, zdjęciu albo OCR nie pokazuje polskich znaków, dodaj je z kontekstu języka polskiego.
- "zelatyna" zwróć jako "żelatyna".
- "maka" zwróć jako "mąka", jeśli z kontekstu chodzi o produkt spożywczy.
- "smietana" zwróć jako "śmietana".
- "wedlina" zwróć jako "wędlina".
- "ogorek" zwróć jako "ogórek".
- "jablko" zwróć jako "jabłko".
- "miod" zwróć jako "miód".
- "roze" zwróć jako "róże".
- Nie usuwaj polskich znaków. Nie zamieniaj "ż" na "z", "ł" na "l", "ó" na "o", "ą" na "a" itd.

Zasady klasyfikacji:
- Każdy produkt musi mieć category i subcategory.
- category i subcategory muszą być wybrane wyłącznie z listy dozwolonych kategorii.
- Zwracaj category i subcategory dokładnie tak, jak są zapisane na liście, razem z polskimi znakami.
- Nie twórz własnych kategorii ani podkategorii.
- Nie istnieje kategoria "Inne" ani podkategoria "inne". Nie wolno ich używać.
- Jeżeli produkt jest trudny, techniczny albo pomocniczy, wybierz najlepszą konkretną kategorię z listy, np. Opakowania i torby, Opłaty techniczne, Promocje i korekty, Usługi albo Nieczytelne pozycje.
- Jeżeli produkt jest żywnością, wybierz najbliższą podkategorię w Żywność.
- Nie klasyfikuj całego paragonu według sklepu. Klasyfikuj każdy produkt osobno.

Przykłady klasyfikacji produktów spożywczych:
- jaja, jajka, jaja kurze -> Żywność / jaja
- żelatyna, galaretka, kisiel, budyń, proszek do pieczenia, drożdże, cukier wanilinowy -> Żywność / dodatki do pieczenia
- mąka, cukier, ryż, sól -> Żywność / produkty sypkie
- makaron, kasza, płatki owsiane -> Żywność / makarony i kasze
- miód -> Żywność / miód
- mleko, śmietana, kefir -> Żywność / nabiał
- ser, twaróg, mozzarella -> Żywność / sery
- jogurt -> Żywność / jogurty
- masło -> Żywność / masło
- chleb, bułki, bagietka -> Żywność / pieczywo
- jabłka, banany, gruszki, truskawki -> Żywność / owoce
- pomidory, ogórki, ziemniaki, marchew -> Żywność / warzywa
- szynka, kiełbasa, parówki -> Żywność / wędliny
- kurczak, wołowina, wieprzowina -> Żywność / mięso
- czekolada, ciastka, cukierki, lody -> Żywność / słodycze
- woda, sok, cola, napój -> Żywność / napoje

Przykłady innych klasyfikacji:
- róże, tulipany, bukiet, kwiaty -> Dom / kwiaty
- reklamówka, torba sklepowa -> Opakowania i torby / reklamówki
- torba papierowa -> Opakowania i torby / torby papierowe
- kaucja, depozyt za butelkę -> Opłaty techniczne / kaucja
- opłata za dostawę -> Opłaty techniczne / opłata dostawy
- rabat, kupon, korekta ceny -> Promocje i korekty / rabat albo Promocje i korekty / korekta ceny
- papier toaletowy -> Dom / papier toaletowy
- płyn do prania, kapsułki do prania -> Dom / pranie
- płyn do naczyń, domestos, środek czystości -> Dom / środki czystości
- szampon, mydło, pasta do zębów -> Higiena / higiena osobista
- baterie -> Elektronika i akcesoria / baterie
- koperta, długopis, zeszyt -> Biuro i papiernicze / artykuły piśmiennicze
- piwo -> Alkohol / piwo
- wino -> Alkohol / wino
- wódka, whisky, rum, gin -> Alkohol / mocny alkohol
- lotto, zakłady sportowe -> Hazard / lotto albo Hazard / zakłady sportowe

Ważne zasady cen:
- paid_price to cena faktycznie zapłacona za pozycję.
- regular_price to cena bez promocji, jeśli jest możliwa do rozpoznania.
- discount_amount to oszczędność na pozycji, jeśli jest możliwa do rozpoznania, w przeciwnym razie 0.00.
- Jeżeli nie widać ilości, quantity może być null.
- Jeżeli nie widać ceny jednostkowej, unit_price może być null.

Dozwolone kategorie i podkategorie — nie wolno pominąć tej listy i nie wolno tworzyć nic poza nią:
{allowed_categories_prompt_text()}
'''

JSON_SCHEMA = {
    'name': 'receipt_ocr',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'merchant_name': {'type': 'string'},
            'purchased_at': {'type': ['string', 'null']},
            'total_amount': {'type': ['number', 'string', 'null']},
            'currency': {'type': 'string'},
            'payment_method': {'type': 'string'},
            'items': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
                'name': {'type': 'string'},
                'quantity': {'type': ['number', 'string', 'null']},
                'unit_price': {'type': ['number', 'string', 'null']},
                'paid_price': {'type': ['number', 'string', 'null']},
                'regular_price': {'type': ['number', 'string', 'null']},
                'discount_amount': {'type': ['number', 'string', 'null']},
                'promotion_name': {'type': 'string'},
                'is_discounted': {'type': 'boolean'},
                'category': {'type': 'string'},
                'subcategory': {'type': 'string'},
            }, 'required': ['name', 'quantity', 'unit_price', 'paid_price', 'regular_price', 'discount_amount', 'promotion_name', 'is_discounted', 'category', 'subcategory']}}
        },
        'required': ['merchant_name', 'purchased_at', 'total_amount', 'currency', 'payment_method', 'items']
    },
    'strict': True,
}


def _clean_item(item):
    name = (item.get('name') or '').strip()
    if not name:
        raise ReceiptParseError('Pozycja paragonu bez nazwy produktu.')
    category, subcategory = normalize_category(item.get('category'), item.get('subcategory'))
    return {
        'name': name,
        'quantity': _number(item.get('quantity')),
        'unit_price': _money(item.get('unit_price')),
        'paid_price': _money(item.get('paid_price')) or '0.00',
        'regular_price': _money(item.get('regular_price')),
        'discount_amount': _money(item.get('discount_amount')) or '0.00',
        'promotion_name': item.get('promotion_name') or '',
        'is_discounted': bool(item.get('is_discounted')),
        'category': category,
        'subcategory': subcategory,
    }


def _clean_response(data):
    items = data.get('items') or []
    if not isinstance(items, list) or not items:
        raise ReceiptParseError('OpenAI nie zwrócił listy pozycji paragonu.')
    return {
        'merchant_name': data.get('merchant_name') or '',
        'purchased_at': data.get('purchased_at'),
        'total_amount': _money(data.get('total_amount')),
        'currency': data.get('currency') or 'PLN',
        'payment_method': data.get('payment_method') or 'unknown',
        'items': [_clean_item(item) for item in items if isinstance(item, dict)],
    }


def _call_openai(client, b64, extra_instruction=''):
    user_text = 'Przeanalizuj paragon i zwróć JSON zgodny ze schematem. Dodaj polskie znaki z kontekstu, jeżeli OCR ich nie pokazuje. Każda pozycja musi mieć poprawną kategorię i podkategorię z listy. Nie używaj Inne/inne, bo takie kategorie nie istnieją.'
    if extra_instruction:
        user_text += '\n\nPoprzedni wynik był niepoprawny: ' + extra_instruction
    response = client.chat.completions.create(
        model=getattr(settings, 'OPENAI_RECEIPT_MODEL', 'gpt-4.1-mini'),
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': user_text},
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}
            ]},
        ],
        response_format={'type': 'json_schema', 'json_schema': JSON_SCHEMA},
        temperature=0,
    )
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
        except (ValueError, ReceiptParseError, json.JSONDecodeError) as error:
            last_error = error
    raise ReceiptParseError(f'Nie udało się poprawnie zdekodować paragonu po 3 próbach: {last_error}')
