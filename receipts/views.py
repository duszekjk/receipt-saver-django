from django.db.models import Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from rest_framework import permissions, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import MatchCandidate, Receipt
from .serializers import MatchCandidateSerializer, ReceiptSerializer
from .services import create_receipt_from_image


class ReceiptViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReceiptSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Receipt.objects.filter(user=self.request.user).prefetch_related('items').order_by('-purchased_at', '-id')


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def scan_receipt(request):
    image = request.FILES.get('image')
    if not image:
        return Response({'error': 'Missing image'}, status=400)
    receipt = create_receipt_from_image(request.user, image)
    return Response(ReceiptSerializer(receipt).data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def summaries(request):
    period = request.query_params.get('period', 'month')
    trunc = {'month': TruncMonth, 'quarter': TruncQuarter, 'halfyear': TruncQuarter, 'year': TruncYear}.get(period, TruncMonth)
    qs = Receipt.objects.filter(user=request.user, duplicate_of__isnull=True, purchased_at__isnull=False)
    rows = qs.annotate(period=trunc('purchased_at')).values('period').annotate(spent=Sum('total_amount'), saved=Sum('items__discount_amount')).order_by('-period')
    result = []
    for row in rows:
        item = {'period': row['period'], 'spent': row['spent'] or 0, 'saved': row['saved'] or 0}
        if period == 'halfyear' and row['period']:
            item['halfyear'] = 1 if row['period'].month <= 6 else 2
        result.append(item)
    return Response(result)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def match_candidates(request):
    qs = MatchCandidate.objects.filter(receipt__user=request.user, status='needs_review').select_related('receipt', 'bank_transaction').order_by('-score')
    return Response(MatchCandidateSerializer(qs, many=True).data)
