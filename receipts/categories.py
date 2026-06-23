import unicodedata

RECEIPT_CATEGORIES = {
    'Żywność': [
        'owoce', 'warzywa', 'pieczywo', 'nabiał', 'jaja', 'sery', 'jogurty', 'masło',
        'mięso', 'wędliny', 'ryby', 'mrożonki', 'produkty sypkie', 'makarony i kasze',
        'dodatki do pieczenia', 'przyprawy', 'słodycze', 'miód', 'dżemy i kremy',
        'napoje', 'woda', 'soki', 'kawa', 'herbata', 'gotowe dania', 'konserwy',
        'sosy i dodatki', 'inne'
    ],
    'Zdrowie': [
        'lekarz', 'dentysta', 'okulista', 'rehabilitacja', 'apteka', 'leki',
        'suplementy', 'sprzęt medyczny', 'badania', 'inne'
    ],
    'Dom': [
        'chemia domowa', 'środki czystości', 'papier toaletowy', 'ręczniki papierowe',
        'pranie', 'kuchnia', 'remont', 'narzędzia', 'ogród', 'kwiaty', 'dekoracje', 'inne'
    ],
    'Higiena': ['kosmetyki', 'higiena osobista', 'fryzjer', 'inne'],
    'Transport': ['paliwo', 'parking', 'komunikacja miejska', 'taxi', 'serwis samochodu', 'inne'],
    'Mieszkanie': ['prąd', 'gaz', 'woda', 'internet', 'telefon', 'czynsz', 'ogrzewanie', 'śmieci', 'inne'],
    'Restauracje': ['restauracja', 'fast food', 'kawiarnia', 'cukiernia', 'inne'],
    'Ubrania': ['odzież', 'obuwie', 'bielizna', 'naprawa ubrań', 'inne'],
    'Zwierzęta': ['karma', 'weterynarz', 'leki', 'akcesoria', 'inne'],
    'Hobby': ['książki', 'ogród', 'rękodzieło', 'sport', 'elektronika', 'prasa', 'inne'],
    'Rodzina': ['dzieci', 'wnuki', 'prezenty', 'uroczystości', 'inne'],
    'Wydarzenia': ['pielgrzymki', 'rekolekcje', 'spotkania wspólnoty', 'konferencje', 'wyjazdy', 'wolontariat', 'inne'],
    'Finanse': ['bank', 'ubezpieczenie', 'podatki', 'opłata', 'inne'],
    'Darowizny': ['kościół', 'wspólnota', 'fundacja', 'rodzina', 'inne'],
    'Edukacja': ['studia', 'kursy', 'książki', 'szkolenia', 'inne'],
    'Subskrypcje': ['internetowe', 'streaming', 'oprogramowanie', 'aplikacje', 'inne'],
    'Hazard': ['lotto', 'zakłady sportowe', 'kasyno', 'poker', 'automaty', 'gry online', 'inne'],
    'Alkohol': ['piwo', 'wino', 'mocny alkohol', 'likier', 'cydr', 'drinki', 'inne'],
    'Inne': ['inne'],
}

BANK_TRANSACTION_CATEGORIES = RECEIPT_CATEGORIES | {
    'Przychody': ['wynagrodzenie', 'emerytura', 'renta', 'zwrot', 'darowizna otrzymana', 'sprzedaż', 'inne'],
    'Przelewy wewnętrzne': ['konto własne', 'oszczędności', 'walutowe', 'inne'],
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
