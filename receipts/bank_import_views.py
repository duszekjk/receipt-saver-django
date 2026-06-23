from django.db import transaction
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .bank_parsers import parse_bank_csv
from .models import BankTransaction
from .openai_bank_transactions import BankClassificationError, classify_bank_statement_rows
from .services import match_bank_transactions_for_receipt
from .utils import normalize_text
from .views import API_AUTHENTICATION, user_family, visible_receipts


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def import_bank_statement(request):
    file = request.FILES.get('file')
    bank = request.data.get('bank', 'unknown')
    if not file:
        return Response({'detail': 'Missing file'}, status=400)

    try:
        parsed_rows = list(parse_bank_csv(file, bank))
        classifications = classify_bank_statement_rows(bank, parsed_rows)
    except BankClassificationError as error:
        return Response({'detail': 'Nie udało się zaimportować wyciągu: ' + str(error)}, status=400)
    except ValueError as error:
        return Response({'detail': 'Nie udało się zaimportować wyciągu: ' + str(error)}, status=400)

    if not parsed_rows:
        return Response({'detail': 'Nie znaleziono żadnych transakcji w pliku wyciągu.'}, status=400)

    family = user_family(request.user)
    with transaction.atomic():
        created = 0
        for row, data in zip(parsed_rows, classifications):
            tx = BankTransaction.objects.create(user=request.user, family=family, bank=bank, source_file_name=file.name, **row)
            tx.corrected_description = data.get('corrected_description') or tx.raw_description or tx.merchant_name or ''
            tx.merchant_name = tx.merchant_name or data.get('merchant_name') or ''
            tx.merchant_normalized = normalize_text(tx.merchant_name)
            tx.transaction_type = data.get('transaction_type') or ''
            tx.category = data.get('category') or ''
            tx.subcategory = data.get('subcategory') or ''
            tx.classification_source = 'openai'
            tx.raw_classification_json = data
            tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
            created += 1

        for receipt in visible_receipts(request.user).filter(duplicate_of__isnull=True):
            match_bank_transactions_for_receipt(receipt)

    return Response({'created': created, 'classified': len(classifications)})
