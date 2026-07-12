import uuid

from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import AppLoginToken, ReceiptUserProfile


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def register_guest(request):
    suffix = uuid.uuid4().hex[:8]
    profile = ReceiptUserProfile.objects.create(
        user=None,
        family=None,
        is_guest=True,
        display_name=f'Gość {suffix}',
        role=ReceiptUserProfile.ROLE_MEMBER,
    )
    token = AppLoginToken.create_for_profile(profile, name='Guest iOS device')
    return Response({
        'type': 'receipt_saver_login',
        'device_id': str(token.device_id),
        'secret_key': token.secret_key,
        'profile_id': profile.id,
        'profile_public_id': str(profile.public_id),
        'display_name': profile.display_name,
        'is_guest': True,
    }, status=201)
