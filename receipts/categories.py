import unicodedata

RECEIPT_CATEGORIES = {
    'Żywność': ['owoce', 'warzywa', 'pieczywo', 'nabiał', 'sery', 'jogurty', 'mięso', 'wędliny', 'ryby', 'mrożonki', 'słodycze', 'napoje', 'woda', 'soki', 'kawa', 'herbata', 'przyprawy', 'miód', 'gotowe dania', 'inne'],
    'Zdrowie': ['lekarz', 'dentysta', 'okulista', 'rehabilitacja', 'apteka', 'leki', 'suplementy', 'sprzęt medyczny', 'badania', 'inne'],
    'Dom': ['chemia domowa', 'środki czystości', 'papier toaletowy', 'ręczniki papierowe', 'pranie', 'kuchnia', 'remont', 'narzędzia', 'ogród', 'kwiaty', 'dekoracje', 'inne'],
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


def _alias_map(categories):
    result = {}
    for category, subcategories in categories.items():
        result[ascii_key(category)] = category
        result[category.strip().lower()] = category
        for subcategory in subcategories:
            result[ascii_key(subcategory)] = subcategory
            result[subcategory.strip().lower()] = subcategory
    return result


def _category_lookup(categories):
    return {ascii_key(category): category for category in categories.keys()} | {category.strip().lower(): category for category in categories.keys()}


def _subcategory_lookup(categories, category):
    return {ascii_key(sub): sub for sub in categories[category]} | {sub.strip().lower(): sub for sub in categories[category]}


def normalize_category(category, subcategory):
    return normalize_from_categories(category, subcategory, RECEIPT_CATEGORIES)


def normalize_bank_category(category, subcategory):
    return normalize_from_categories(category, subcategory, BANK_TRANSACTION_CATEGORIES)


def normalize_from_categories(category, subcategory, categories):
    category_lookup = _category_lookup(categories)
    canonical_category = category_lookup.get(ascii_key(category)) or category_lookup.get((category or '').strip().lower())
    if not canonical_category:
        return 'Inne', 'inne'
    sub_lookup = _subcategory_lookup(categories, canonical_category)
    canonical_subcategory = sub_lookup.get(ascii_key(subcategory)) or sub_lookup.get((subcategory or '').strip().lower())
    if not canonical_subcategory:
        return canonical_category, 'inne'
    return canonical_category, canonical_subcategory


def infer_category_from_name(name, category=None, subcategory=None):
    category, subcategory = normalize_category(category, subcategory)
    if category != 'Inne' or subcategory != 'inne':
        return category, subcategory

    text = f' {ascii_key(name)} '
    rules = [
        (['miod', 'miod ', 'miod wielokwiatowy'], 'Żywność', 'miód'),
        (['roza', 'roze', 'bukiet', 'kwiat', 'kwiaty'], 'Dom', 'kwiaty'),
        (['chleb', 'bulka', 'kajzerka', 'bagietka'], 'Żywność', 'pieczywo'),
        (['maslo', 'mleko', 'jogurt', 'ser ', 'twarog', 'smietana'], 'Żywność', 'nabiał'),
        (['jablko', 'banan', 'gruszka', 'truskawka', 'borowka', 'malina'], 'Żywność', 'owoce'),
        (['pomidor', 'ogorek', 'cebula', 'ziemniak', 'marchew', 'salata'], 'Żywność', 'warzywa'),
        (['kurczak', 'wolowina', 'wieprz', 'mieso'], 'Żywność', 'mięso'),
        (['szynka', 'kielbasa', 'parowki', 'wedlina'], 'Żywność', 'wędliny'),
        (['czekolada', 'ciastka', 'cukierki', 'lody'], 'Żywność', 'słodycze'),
        (['woda', 'sok', 'cola', 'napoj'], 'Żywność', 'napoje'),
        (['kawa', 'herbata'], 'Żywność', 'kawa'),
        (['piwo'], 'Alkohol', 'piwo'),
        (['wino'], 'Alkohol', 'wino'),
        (['wodka', 'whisky', 'rum', 'gin'], 'Alkohol', 'mocny alkohol'),
        (['paliwo', 'benzyna', 'diesel', 'pb95', 'pb 95', 'orlen', 'shell', 'bp '], 'Transport', 'paliwo'),
        (['papier toaletowy', 'recznik papierowy'], 'Dom', 'papier toaletowy'),
        (['proszek', 'plyn do prania', 'domestos', 'ludwik'], 'Dom', 'środki czystości'),
        (['szampon', 'mydlo', 'pasta do zebow', 'dezodorant'], 'Higiena', 'higiena osobista'),
    ]
    for needles, mapped_category, mapped_subcategory in rules:
        if any(f' {needle} ' in text or text.strip() == needle for needle in needles):
            return mapped_category, mapped_subcategory
    return category, subcategory


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
