from django import forms


class BankStatementImportForm(forms.Form):
    BANK_CHOICES = [
        ('ing', 'ING'),
        ('santander', 'Santander'),
        ('revolut', 'Revolut'),
    ]

    bank = forms.ChoiceField(label='Bank', choices=BANK_CHOICES)
    file = forms.FileField(label='Plik wyciągu CSV/XLS/XLSX')
