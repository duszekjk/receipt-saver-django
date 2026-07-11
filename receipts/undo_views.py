from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .undo_service import latest_undo, serialize_undo, undo_latest

API_AUTHENTICATION = [AppTokenAuthentication]


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def undo_status(request):
    return Response(serialize_undo(latest_undo(request.user)))


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def undo(request):
    try:
        operation = undo_latest(request.user)
    except ValueError as error:
        return Response({'detail': str(error)}, status=409)
    if not operation:
        return Response({'detail': 'Nie ma operacji do cofnięcia.'}, status=409)
    result = serialize_undo(latest_undo(request.user))
    result['undone_label'] = operation.label
    return Response(result)