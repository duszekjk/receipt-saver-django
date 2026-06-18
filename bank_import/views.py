from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from receipts.models import BankTransaction, Receipt
from receipts.services import match_bank_transactions_for_receipt
from .parsers import parse_bank_csv


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def import_bank_statement(request):
    file = request.FILES.get('file')
    bank = request.data.get('bank', 'unknown')
    if not file:
        return Response({'error': 'Missing file'}, status=400)
    created = 0
    for row in parse_bank_csv(file, bank):
        BankTransaction.objects.create(user=request.user, bank=bank, source_file_name=file.name, **row)
        created += 1
    for receipt in Receipt.objects.filter(user=request.user, duplicate_of__isnull=True):
        match_bank_transactions_for_receipt(receipt)
    return Response({'created': created})
