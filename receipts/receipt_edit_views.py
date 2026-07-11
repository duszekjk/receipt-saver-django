from django.db import transaction
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .serializers import ReceiptSerializer
from .views import visible_receipts


API_AUTHENTICATION = [AppTokenAuthentication]


def _visible_receipt(user, receipt_id):
    return visible_receipts(user).prefetch_related('items').filter(id=receipt_id).first()


@api_view(['PATCH'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def update_receipt(request, receipt_id):
    receipt = _visible_receipt(request.user, receipt_id)
    if not receipt:
        return Response({'detail': 'Paragon nie istnieje.'}, status=404)

    serializer = ReceiptSerializer(receipt, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    updated = serializer.save()
    return Response(ReceiptSerializer(updated).data)


@api_view(['DELETE'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def delete_receipt(request, receipt_id):
    receipt = _visible_receipt(request.user, receipt_id)
    if not receipt:
        return Response({'detail': 'Paragon nie istnieje.'}, status=404)

    image = receipt.image
    with transaction.atomic():
        receipt.delete()
    if image:
        image.delete(save=False)
    return Response(status=204)
