import unicodedata


RECEIPT_CATEGORIES = {
    'Żywność': [
        'owoce', 'warzywa', 'pieczywo', 'nabiał', 'jaja', 'sery', 'jogurty', 'masło',
        'mięso', 'wędliny', 'ryby', 'mrożonki', 'produkty sypkie', 'makarony i kasze',
        'dodatki do pieczenia', 'przyprawy', 'słodycze', 'miód', 'dżemy i kremy',
        'napoje', 'woda', 'soki', 'kawa', 'herbata', 'gotowe dania', 'konserwy',
        'sosy i dodatki'
    ],
    'Zdrowie': [
        'lekarz', 'dentysta', 'okulista', 'rehabilitacja', 'apteka', 'leki',
        'suplementy', 'sprzęt medyczny', 'badania'
    ],
    'Dom': [
        'chemia domowa', 'środki czystości', 'papier toaletowy', 'ręczniki papierowe',
        'pranie', 'kuchnia', 'wyposażenie domu', 'meble', 'remont', 'narzędzia',
        'ogród', 'kwiaty', 'dekoracje'
    ],
    'Higiena': ['kosmetyki', 'higiena osobista', 'fryzjer'],
    'Transport': [
        'paliwo', 'parking', 'opłaty drogowe', 'komunikacja miejska', 'kolej',
        'taxi', 'serwis samochodu', 'części samochodowe', 'myjnia'
    ],
    'Mieszkanie': ['prąd', 'gaz', 'woda', 'internet', 'telefon', 'czynsz', 'ogrzewanie', 'śmieci'],
    'Restauracje': ['restauracja', 'fast food', 'kawiarnia', 'cukiernia'],
    'Ubrania': ['odzież', 'obuwie', 'bielizna', 'naprawa ubrań'],
    'Akcesoria osobiste': ['parasole', 'portfele', 'okulary', 'zegarki', 'biżuteria', 'torby i plecaki'],
    'Zwierzęta': ['karma', 'weterynarz', 'leki', 'akcesoria', 'pielęgnacja'],
    'Sport i rekreacja': ['sprzęt sportowy', 'siłownia', 'basen', 'zajęcia sportowe', 'rekreacja'],
    'Kultura i media': ['książki', 'filmy', 'muzyka', 'gry', 'prasa'],
    'Hobby': ['ogród', 'rękodzieło', 'kolekcjonerstwo', 'elektronika', 'modelarstwo'],
    'Rodzina': ['dzieci', 'wnuki', 'opieka nad dziećmi', 'prezenty', 'uroczystości'],
    'Wydarzenia': ['pielgrzymki', 'rekolekcje', 'spotkania wspólnoty', 'konferencje', 'wyjazdy', 'wolontariat'],
    'Finanse': ['bank', 'ubezpieczenie', 'podatki', 'opłata finansowa', 'kredyt i pożyczka'],
    'Darowizny': ['kościół', 'wspólnota', 'fundacja', 'rodzina'],
    'Edukacja': ['studia', 'kursy', 'materiały edukacyjne', 'szkolenia'],
    'Subskrypcje': ['streaming wideo', 'streaming muzyki', 'chmura', 'oprogramowanie', 'aplikacje', 'prasa cyfrowa', 'AI'],
    'Hazard': ['lotto', 'zakłady sportowe', 'kasyno', 'poker', 'automaty', 'gry online'],
    'Alkohol': ['piwo', 'wino', 'mocny alkohol', 'likier', 'cydr', 'drinki'],
    'Opakowania i torby': ['reklamówki', 'torby papierowe', 'torby wielorazowe', 'opakowania', 'pojemniki'],
    'Opłaty techniczne': ['kaucja', 'depozyt', 'opłata serwisowa', 'opłata manipulacyjna', 'opłata dostawy'],
    'Promocje i korekty': ['rabat', 'kupon', 'zwrot', 'korekta ceny', 'zaokrąglenie'],
    'Usługi': ['usługi sklepowe', 'naprawy', 'czyszczenie', 'drukowanie', 'dorabianie kluczy', 'usługi profesjonalne'],
    'Biuro i papiernicze': ['papier', 'artykuły piśmiennicze', 'druk', 'koperty', 'organizacja dokumentów'],
    'Elektronika i akcesoria': ['baterie', 'kable', 'ładowarki', 'akcesoria telefoniczne', 'akcesoria komputerowe', 'sprzęt elektroniczny'],
    'Prezenty': ['upominki', 'kartki okolicznościowe', 'pakowanie prezentów'],
    'Podróże': ['noclegi', 'bilety', 'bagaż', 'ubezpieczenie podróżne', 'wycieczki'],
    'Administracyjne': ['urząd', 'poczta', 'mandaty', 'dokumenty', 'opłaty urzędowe'],
    'Nieczytelne pozycje': ['pozycja nieczytelna', 'skrót nierozpoznany', 'produkt niejednoznaczny'],
}


CATEGORY_SYNONYMS = {
    ('Transport', 'opłaty drogowe'): [
        'autostrada', 'autostrady', 'winieta', 'winiety', 'e-toll', 'etoll',
        'bramka', 'bramki autostradowe', 'opłata za przejazd', 'droga płatna',
        'płatny tunel', 'płatny most', 'amberone', 'autopay'
    ],
    ('Transport', 'parking'): ['postój', 'parkomat', 'mobilet', 'strefa parkowania'],
    ('Transport', 'paliwo'): ['benzyna', 'diesel', 'olej napędowy', 'tankowanie', 'stacja paliw'],
    ('Finanse', 'ubezpieczenie'): ['polisa', 'oc', 'ac', 'nnw', 'ubezpieczenia', 'składka ubezpieczeniowa'],
    ('Kultura i media', 'książki'): ['książka', 'ebook', 'e-book', 'kindle', 'publio', 'legimi', 'audiobook'],
    ('Kultura i media', 'filmy'): ['film', 'movie', 'dvd', 'blu-ray', 'bluray', 'vod', 'apple tv', 'wypożyczenie filmu'],
    ('Kultura i media', 'muzyka'): ['album', 'płyta', 'cd', 'winyl', 'vinyl', 'itunes', 'utwór'],
    ('Kultura i media', 'gry'): ['gra', 'steam', 'playstation', 'xbox', 'nintendo', 'dlc'],
    ('Subskrypcje', 'streaming wideo'): ['netflix', 'max', 'disney+', 'prime video', 'abonament filmowy'],
    ('Subskrypcje', 'streaming muzyki'): ['spotify', 'apple music', 'tidal', 'youtube music'],
    ('Subskrypcje', 'chmura'): ['icloud', 'icloud+', 'google one', 'dropbox', 'onedrive', 'dysk w chmurze'],
    ('Subskrypcje', 'AI'): ['chatgpt', 'openai', 'claude', 'gemini', 'sztuczna inteligencja'],
    ('Dom', 'wyposażenie domu'): ['wyposażenie', 'artykuły domowe', 'gospodarstwo domowe'],
    ('Sport i rekreacja', 'siłownia'): ['fitness', 'gym', 'karnet sportowy'],
    ('Administracyjne', 'opłaty urzędowe'): ['opłata skarbowa', 'urząd', 'wniosek', 'administracja'],
}


BANK_TRANSACTION_CATEGORIES = RECEIPT_CATEGORIES | {
    'Przychody': ['wynagrodzenie', 'emerytura', 'renta', 'zwrot', 'darowizna otrzymana', 'sprzedaż', 'pozostałe przychody'],
    'Przelewy wewnętrzne': ['konto własne', 'oszczędności', 'walutowe', 'Revolut', 'kieszeń Revolut', 'karta kredytowa', 'wymiana walut'],
}


def ascii_key(value):
    value = (value or '').strip().lower().replace('_', ' ').replace('-', ' ')
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return ' '.join(value.split())


def _category_lookup(categories):
    result = {}
    for category in categories.keys():
        result[ascii_key(category)] = category
        result[category.strip().lower()] = category
    return result


def _subcategory_lookup(categories, category):
    result = {}
    for subcategory in categories[category]:
        result[ascii_key(subcategory)] = subcategory
        result[subcategory.strip().lower()] = subcategory
    return result


def normalize_category(category, subcategory):
    return normalize_from_categories(category, subcategory, RECEIPT_CATEGORIES)


def normalize_bank_category(category, subcategory):
    return normalize_from_categories(category, subcategory, BANK_TRANSACTION_CATEGORIES)


def normalize_from_categories(category, subcategory, categories):
    category_lookup = _category_lookup(categories)
    canonical_category = category_lookup.get(ascii_key(category)) or category_lookup.get((category or '').strip().lower())
    if not canonical_category:
        raise ValueError(f'Niepoprawna kategoria: {category!r}')
    sub_lookup = _subcategory_lookup(categories, canonical_category)
    canonical_subcategory = sub_lookup.get(ascii_key(subcategory)) or sub_lookup.get((subcategory or '').strip().lower())
    if not canonical_subcategory:
        raise ValueError(f'Niepoprawna podkategoria dla {canonical_category}: {subcategory!r}')
    return canonical_category, canonical_subcategory


def category_is_valid(category, subcategory):
    try:
        normalize_category(category, subcategory)
        return True
    except ValueError:
        return False


def allowed_categories_prompt_text():
    return '\n'.join(
        f'- {category}: {", ".join(subcategories)}'
        for category, subcategories in RECEIPT_CATEGORIES.items()
    )


def allowed_bank_categories_prompt_text():
    return '\n'.join(
        f'- {category}: {", ".join(subcategories)}'
        for category, subcategories in BANK_TRANSACTION_CATEGORIES.items()
    )


def category_catalog_payload():
    return {
        'version': 2,
        'categories': [
            {
                'name': category,
                'subcategories': [
                    {
                        'name': subcategory,
                        'synonyms': CATEGORY_SYNONYMS.get((category, subcategory), []),
                    }
                    for subcategory in subcategories
                ],
            }
            for category, subcategories in RECEIPT_CATEGORIES.items()
        ],
    }
