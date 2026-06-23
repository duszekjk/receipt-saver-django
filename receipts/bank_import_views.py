from django.db import transaction
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .bank_parsers import parse_bank_csv
from .models import BankTransaction
from .openai_bank_transactions import BankClassificationError, apply_bank_transaction_classification
from .services import match_bank_transactions_for_receipt
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
    except Exception as error:
        return Response({'detail': f'Nie udało się odczytać pliku wyciągu: {error}'}, status=400)

    if not parsed_rows:
        return Response({'detail': 'Nie znaleziono żadnych transakcji w pliku wyciągu.'}, status=400)

    family = user_family(request.user)
    try:
        with transaction.atomic():
            created = 0
            classified = 0
            for row in parsed_rows:
                tx = BankTransaction.objects.create(
                    user=request.user,
                    family=family,
                    bank=bank,
                    source_file_name=file.name,
                    **row,
                )
                apply_bank_transaction_classification(tx)
                created += 1
                classified += 1

            for receipt in visible_receipts(request.user).filter(duplicate_of__isnull=True):
                match_bank_transactions_for_receipt(receipt)

        return Response({'created': created, 'classified': classified})
    except BankClassificationError as error:
        return Response({'detail': f'Nie udało się sklasyfikować transakcji bankowej: {error}'}, status=400)
    except ValueError as error:
        return Response({'detail': f'Nie udało się zaimportować wyciągu: {error}'}, status=400)
