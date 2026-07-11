from decimal import Decimal, InvalidOperation

from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .categories import normalize_bank_category
from .views import visible_bank_transactions


API_AUTHENTICATION = [AppTokenAuthentication]


def _serialize(tx):
    raw = tx.raw_classification_json or {}
    return {
        'transaction_id': tx.id,
        'merchant_name': tx.merchant_name or '',
        'description': tx.corrected_description or tx.raw_description or '',
        'amount': str(abs(tx.amount)),
        'currency': tx.currency or 'PLN',
        'items': raw.get('manual_items') or [],
    }


@api_view(['GET', 'PUT'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def bank_transaction_items(request, transaction_id):
    tx = visible_bank_transactions(request.user).filter(id=transaction_id).first()
    if not tx:
        return Response({'detail': 'Transakcja bankowa nie istnieje.'}, status=404)

    if request.method == 'GET':
        return Response(_serialize(tx))

    raw_items = request.data.get('items')
    if not isinstance(raw_items, list):
        return Response({'detail': 'Pole items musi być listą.'}, status=400)

    items = []
    total = Decimal('0.00')
    for index, source in enumerate(raw_items):
        if not isinstance(source, dict):
            return Response({'detail': f'Pozycja {index + 1} ma niepoprawny format.'}, status=400)
        name = str(source.get('name') or '').strip()
        if not name:
            return Response({'detail': f'Pozycja {index + 1} nie ma nazwy.'}, status=400)
        try:
            amount = Decimal(str(source.get('amount') or '').replace(',', '.'))
        except (InvalidOperation, ValueError):
            return Response({'detail': f'Pozycja {index + 1} ma niepoprawną kwotę.'}, status=400)
        if amount < 0:
            amount = abs(amount)
        try:
            category, subcategory = normalize_bank_category(source.get('category'), source.get('subcategory'))
        except ValueError as error:
            return Response({'detail': str(error)}, status=400)
        items.append({
            'name': name,
            'amount': str(amount.quantize(Decimal('0.01'))),
            'category': category,
            'subcategory': subcategory,
        })
        total += amount

    expected = abs(tx.amount)
    if items and abs(total - expected) > Decimal('0.01'):
        return Response({
            'detail': f'Suma pozycji ({total:.2f} {tx.currency}) musi być równa kwocie transakcji ({expected:.2f} {tx.currency}).'
        }, status=400)

    raw = dict(tx.raw_classification_json or {})
    raw['manual_items'] = items
    tx.raw_classification_json = raw
    tx.classification_source = 'manual_items' if items else tx.classification_source
    tx.save(update_fields=['raw_classification_json', 'classification_source'])
    return Response(_serialize(tx))
