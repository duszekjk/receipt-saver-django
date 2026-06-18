# Receipt Saver Django App

To repo zawiera **jedną aplikację Django** do wpięcia w istniejący projekt: `receipts`.

Nie jest to samodzielny projekt Django. Nie ma tu `manage.py`, ustawień projektu, WSGI ani osobnej aplikacji do importu bankowego.

## Co zawiera aplikacja

- upload zdjęcia paragonu,
- OCR + kategoryzacja produktów przez OpenAI Vision,
- wykrywanie promocji i oszczędności,
- wykrywanie duplikatów paragonów po treści,
- import CSV z ING/Santander,
- probabilistyczne dopasowanie transakcji bankowej do paragonu,
- podsumowania: miesięczne, kwartalne, półroczne, roczne.

## Integracja z istniejącym projektem

1. Skopiuj katalog `receipts/` do głównego projektu Django.
2. Dodaj aplikację do `INSTALLED_APPS`:

```python
INSTALLED_APPS += [
    'receipts',
]
```

3. Podepnij URL-e w głównym `urls.py` projektu:

```python
path('api/', include('receipts.urls')),
```

4. Projekt nadrzędny powinien dostarczać:

```python
OPENAI_KEY = '...'
OPENAI_RECEIPT_MODEL = 'gpt-4o-mini'
```

5. Zależności z `requirements.txt` potraktuj jako fragment do scalenia z zależnościami głównego projektu.

Migracje, baza danych, autoryzacja, media storage i deployment są zarządzane przez główny projekt.
