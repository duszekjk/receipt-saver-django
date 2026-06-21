RECEIPT_CATEGORIES = {
    'zywnosc': ['owoce', 'warzywa', 'pieczywo', 'nabial', 'sery', 'jogurty', 'mieso', 'wedliny', 'ryby', 'mrozonki', 'slodycze', 'napoje', 'woda', 'soki', 'kawa', 'herbata', 'przyprawy', 'gotowe_dania', 'inne'],
    'zdrowie': ['lekarz', 'dentysta', 'okulista', 'rehabilitacja', 'apteka', 'suplementy', 'sprzet_medyczny', 'badania', 'inne'],
    'dom': ['chemia_domowa', 'srodki_czystosci', 'papier_toaletowy', 'reczniki_papierowe', 'pranie', 'kuchnia', 'remont', 'narzedzia', 'ogrod', 'kwiaty', 'dekoracje', 'inne'],
    'higiena': ['kosmetyki', 'higiena_osobista', 'fryzjer', 'inne'],
    'transport': ['paliwo', 'parking', 'komunikacja_miejska', 'taxi', 'serwis_samochodu', 'inne'],
    'mieszkanie': ['prad', 'gaz', 'woda', 'internet', 'telefon', 'czynsz', 'ogrzewanie', 'smieci', 'inne'],
    'restauracje': ['restauracja', 'fast_food', 'kawiarnia', 'cukiernia', 'inne'],
    'ubrania': ['odziez', 'obuwie', 'bielizna', 'naprawa_ubran', 'inne'],
    'zwierzeta': ['karma', 'weterynarz', 'leki', 'akcesoria', 'inne'],
    'hobby': ['ksiazki', 'ogrod', 'rekodzielo', 'sport', 'elektronika', 'prasa', 'inne'],
    'rodzina': ['dzieci', 'wnuki', 'prezenty', 'uroczystosci', 'inne'],
    'wydarzenia': ['pielgrzymki', 'rekolekcje', 'spotkania_wspolnoty', 'konferencje', 'wyjazdy', 'wolontariat', 'inne'],
    'finanse': ['bank', 'ubezpieczenie', 'podatki', 'oplata', 'inne'],
    'darowizny': ['kosciol', 'wspolnota', 'fundacja', 'rodzina', 'inne'],
    'edukacja': ['studia', 'kursy', 'ksiazki', 'szkolenia', 'inne'],
    'subskrypcje': ['internetowe', 'streaming', 'oprogramowanie', 'aplikacje', 'inne'],
    'hazard': ['lotto', 'zaklady_sportowe', 'kasyno', 'poker', 'automaty', 'gry_online', 'inne'],
    'alkohol': ['piwo', 'wino', 'mocny_alkohol', 'likier', 'cydr', 'drinki', 'inne'],
    'inne': ['inne'],
}

BANK_TRANSACTION_CATEGORIES = RECEIPT_CATEGORIES | {
    'przychody': ['wynagrodzenie', 'emerytura', 'renta', 'zwrot', 'darowizna_otrzymana', 'sprzedaz', 'inne'],
    'przelewy_wewnetrzne': ['konto_wlasne', 'oszczednosci', 'walutowe', 'inne'],
}


def normalize_category(category, subcategory):
    category = (category or '').strip().lower()
    subcategory = (subcategory or '').strip().lower()
    if category not in RECEIPT_CATEGORIES:
        return 'inne', 'inne'
    if subcategory not in RECEIPT_CATEGORIES[category]:
        return category, 'inne'
    return category, subcategory


def normalize_bank_category(category, subcategory):
    category = (category or '').strip().lower()
    subcategory = (subcategory or '').strip().lower()
    if category not in BANK_TRANSACTION_CATEGORIES:
        return 'inne', 'inne'
    if subcategory not in BANK_TRANSACTION_CATEGORIES[category]:
        return category, 'inne'
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
