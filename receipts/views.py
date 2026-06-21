from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone
from rest_framework import authentication, permissions, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .authentication import AppTokenAuthentication
from .bank_parsers import parse_bank_csv
from .models import BankTransaction, MatchCandidate, Receipt, ReceiptItem
from .serializers import MatchCandidateSerializer, ReceiptSerializer
from .services import create_receipt_from_image, match_bank_transactions_for_receipt

API_AUTHENTICATION = [AppTokenAuthentication, authentication.SessionAuthentication, authentication.BasicAuthentication]


def user_family(user):
    profile = getattr(user, 'receipt_profile', None)
    return profile.family if profile and profile.family_id else None


def visible_receipts(user):
    if user.is_superuser:
        return Receipt.objects.all()
    family = user_family(user)
    if family:
        return Receipt.objects.filter(family=family)
    return Receipt.objects.filter(user=user)


def period_start(period):
    now = timezone.now()
    if period == 'year':
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == 'halfyear':
        month = 1 if now.month <= 6 else 7
        return now.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == 'quarter':
        month = ((now.month - 1) // 3) * 3 + 1
        return now.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def decimal_value(value):
    return float(value or Decimal('0.00'))


def top_rows(qs, group_key, total_key='paid_price', limit=10):
    rows = list(
        qs.values(group_key)
        .annotate(spent=Sum(total_key), saved=Sum('discount_amount'), count=Count('id'))
        .order_by('-spent')[:limit]
    )
    return [
        {
            'name': row[group_key] or 'inne',
            'spent': decimal_value(row['spent']),
            'saved': decimal_value(row['saved']),
            'count': row['count'],
        }
        for row in rows
    ]


class ReceiptViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReceiptSerializer
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = API_AUTHENTICATION

    def get_queryset(self):
        return visible_receipts(self.request.user).prefetch_related('items').order_by('-purchased_at', '-id')


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def me(request):
    profile = getattr(request.user, 'receipt_profile', None)
    family = user_family(request.user)
    return Response({
        'user_id': request.user.id,
        'username': request.user.get_username(),
        'is_superuser': request.user.is_superuser,
        'profile_id': profile.id if profile else None,
        'display_name': profile.display_name if profile else '',
        'family_id': family.id if family else None,
        'family_name': family.name if family else '',
    })


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def scan_receipt(request):
    image = request.FILES.get('image')
    if not image:
        return Response({'error': 'Missing image'}, status=400)
    receipt = create_receipt_from_image(request.user, image)
    family = user_family(request.user)
    if family and not receipt.family_id:
        receipt.family = family
        receipt.save(update_fields=['family'])
    return Response(ReceiptSerializer(receipt).data)


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def import_bank_statement(request):
    file = request.FILES.get('file')
    bank = request.data.get('bank', 'unknown')
    if not file:
        return Response({'error': 'Missing file'}, status=400)
    family = user_family(request.user)
    created = 0
    for row in parse_bank_csv(file, bank):
        BankTransaction.objects.create(user=request.user, family=family, bank=bank, source_file_name=file.name, **row)
        created += 1
    for receipt in visible_receipts(request.user).filter(duplicate_of__isnull=True):
        match_bank_transactions_for_receipt(receipt)
    return Response({'created': created})


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def summaries(request):
    period = request.query_params.get('period', 'month')
    scope = request.query_params.get('scope', 'family')
    trunc = {'month': TruncMonth, 'quarter': TruncQuarter, 'halfyear': TruncQuarter, 'year': TruncYear}.get(period, TruncMonth)
    qs = visible_receipts(request.user).filter(duplicate_of__isnull=True, purchased_at__isnull=False)
    if scope == 'user':
        qs = qs.filter(user=request.user)
    rows = qs.annotate(period=trunc('purchased_at')).values('period', 'user_id').annotate(spent=Sum('total_amount'), saved=Sum('items__discount_amount')).order_by('-period')
    result = []
    for row in rows:
        item = {'period': row['period'], 'user_id': row['user_id'], 'spent': decimal_value(row['spent']), 'saved': decimal_value(row['saved'])}
        if period == 'halfyear' and row['period']:
            item['halfyear'] = 1 if row['period'].month <= 6 else 2
        result.append(item)
    return Response(result)


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def dashboard(request):
    period = request.query_params.get('period', 'month')
    category_filter = request.query_params.get('category', '')
    try:
        limit = max(3, min(30, int(request.query_params.get('limit', 10))))
    except ValueError:
        limit = 10

    receipts_qs = visible_receipts(request.user).filter(duplicate_of__isnull=True)
    start = period_start(period)
    receipts_qs = receipts_qs.filter(purchased_at__gte=start)
    receipt_ids = receipts_qs.values_list('id', flat=True)
    items_qs = ReceiptItem.objects.filter(receipt_id__in=receipt_ids)

    subcategory_qs = items_qs
    if category_filter:
        subcategory_qs = subcategory_qs.filter(category=category_filter)

    categories = top_rows(items_qs, 'category', limit=limit)
    subcategories = top_rows(subcategory_qs, 'subcategory', limit=limit)
    products = top_rows(items_qs, 'name_normalized', limit=limit)

    stores = list(
        receipts_qs.values('merchant_name')
        .annotate(spent=Sum('total_amount'), count=Count('id'))
        .order_by('-spent')[:limit]
    )

    spent = receipts_qs.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    saved = items_qs.aggregate(total=Sum('discount_amount'))['total'] or Decimal('0.00')
    all_categories = list(items_qs.exclude(category='').values_list('category', flat=True).distinct().order_by('category'))

    return Response({
        'period': period,
        'category_filter': category_filter,
        'cards': {
            'spent': decimal_value(spent),
            'saved': decimal_value(saved),
            'receipt_count': receipts_qs.count(),
            'store_count': receipts_qs.exclude(merchant_name='').values('merchant_name').distinct().count(),
        },
        'available_categories': all_categories,
        'categories': categories,
        'subcategories': subcategories,
        'products': products,
        'stores': [
            {'name': row['merchant_name'] or 'Nieznany sklep', 'spent': decimal_value(row['spent']), 'saved': 0.0, 'count': row['count']}
            for row in stores
        ],
    })


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def match_candidates(request):
    qs = MatchCandidate.objects.filter(receipt__in=visible_receipts(request.user), status='needs_review').select_related('receipt', 'bank_transaction').order_by('-score')
    return Response(MatchCandidateSerializer(qs, many=True).data)
