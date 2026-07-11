import io

from django.db import transaction
from django.http import HttpResponse
from PIL import Image, ImageOps
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .serializers import ReceiptSerializer
from .views import visible_receipts


API_AUTHENTICATION = [AppTokenAuthentication]
PREVIEW_MAX_DIMENSION = 1600
PREVIEW_JPEG_QUALITY = 82


def _visible_receipt(user, receipt_id):
    return visible_receipts(user).prefetch_related('items').filter(id=receipt_id).first()


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def receipt_preview(request, receipt_id):
    receipt = _visible_receipt(request.user, receipt_id)
    if not receipt:
        return Response({'detail': 'Paragon nie istnieje.'}, status=404)
    if not receipt.image:
        return Response({'detail': 'Brak zdjęcia paragonu.'}, status=404)

    try:
        with receipt.image.open('rb') as source:
            image = Image.open(source)
            image = ImageOps.exif_transpose(image)
            if image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')
            elif image.mode == 'L':
                image = image.convert('RGB')
            image.thumbnail((PREVIEW_MAX_DIMENSION, PREVIEW_MAX_DIMENSION), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=PREVIEW_JPEG_QUALITY, optimize=True)
    except (OSError, ValueError):
        return Response({'detail': 'Nie udało się przygotować podglądu zdjęcia.'}, status=500)

    response = HttpResponse(output.getvalue(), content_type='image/jpeg')
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    response['Content-Disposition'] = f'inline; filename="receipt-{receipt.id}-preview.jpg"'
    return response


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
