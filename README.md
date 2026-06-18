# Receipt Saver Django

Backend Django/DRF dla aplikacji do skanowania paragonów, śledzenia oszczędności z promocji oraz luźnego merge z wyciągami ING/Santander.

## Zakres MVP

- upload zdjęcia paragonu,
- OCR + kategoryzacja produktów przez OpenAI Vision,
- wykrywanie promocji i oszczędności,
- wykrywanie duplikatów paragonów po treści, nie po zdjęciu,
- import CSV z ING/Santander,
- probabilistyczne dopasowanie transakcji bankowej do paragonu,
- podsumowania: miesięczne, kwartalne, półroczne, roczne.

## Start lokalny

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

W istniejącym serwisie Django możesz skopiować aplikacje `receipts` i `bank_import`. Backend używa zmiennej `OPENAI_KEY`.
