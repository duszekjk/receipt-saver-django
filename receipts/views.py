from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone
from rest_framework import authentication, permissions, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .authentication import AppTokenAuthentication
from .bank_parsers import parse_bank_csv
from .models import BankTransaction, MatchCandidate, Receipt, ReceiptItem
from .openai_bank_transactions import apply_bank_transaction_classification
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
        return Receipt.objects.filter(Q(family=family) | Q(user=user)).distinct()
    return Receipt.objects.filter(user=user)


def visible_bank_transactions(user):
    if user.is_superuser:
        return BankTransaction.objects.all()
    family = user_family(user)
    if family:
        return BankTransaction.objects.filter(Q(family=family) | Q(user=user)).distinct()
    return BankTransaction.objects.filter(user=user)


def rolling_start(period):
    now = timezone.now()
    if period == 'last30':
        return now - timedelta(days=30)
    if period == 'last90':
        return now - timedelta(days=90)
    return None


def period_trunc(period):
    if period == 'quarter':
        return TruncQuarter
    if period == 'year':
        return TruncYear
    return TruncMonth


def period_label(value, period):
    if not value:
        return ''
    if period == 'quarter':
        quarter = ((value.month - 1) // 3) + 1
        return f'{value.year} Q{quarter}'
    if period == 'year':
        return f'{value.year}'
    return value.strftime('%Y-%m')


def decimal_value(value):
    return float(value or Decimal('0.00'))


def top_rows(qs, group_key, total_key='paid_price', limit=10):
    rows = list(qs.values(group_key).annotate(spent=Sum(total_key), saved=Sum('discount_amount'), count=Count('id')).order_by('-spent')[:limit])
    return [{'name': row[group_key] or 'inne', 'spent': decimal_value(row['spent']), 'saved': decimal_value(row['saved']), 'count': row['count']} for row in rows]


def bank_top_rows(qs, group_key, limit=10):
    rows = list(qs.values(group_key).annotate(spent=Sum('amount'), count=Count('id')).order_by('spent')[:limit])
    return [{'name': row[group_key] or 'inne', 'spent': abs(decimal_value(row['spent'])), 'saved': 0.0, 'count': row['count']} for row in rows]


def merge_rows(primary, fallback, limit):
    merged = {}
    for row in list(primary) + list(fallback):
        name = row['name'] or 'inne'
        if name not in merged:
            merged[name] = {'name': name, 'spent': 0.0, 'saved': 0.0, 'count': 0}
        merged[name]['spent'] += row.get('spent') or 0.0
        merged[name]['saved'] += row.get('saved') or 0.0
        merged[name]['count'] += row.get('count') or 0
    return sorted(merged.values(), key=lambda item: item['spent'], reverse=True)[:limit]


def build_timeline(period, receipt_items_qs, bank_qs):
    trunc = period_trunc(period)
    receipt_rows = receipt_items_qs.filter(receipt__purchased_at__isnull=False).annotate(bucket=trunc('receipt__purchased_at')).values('bucket').annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id'))
    bank_rows = bank_qs.filter(transaction_at__isnull=False).annotate(bucket=trunc('transaction_at')).values('bucket').annotate(spent=Sum('amount'), count=Count('id'))
    merged = {}
    for row in receipt_rows:
        bucket = row['bucket']
        key = period_label(bucket, period)
        merged[key] = {'name': key, 'spent': Decimal('0.00'), 'saved': Decimal('0.00'), 'count': 0, 'sort': bucket}
        merged[key]['spent'] += row['spent'] or Decimal('0.00')
        merged[key]['saved'] += row['saved'] or Decimal('0.00')
        merged[key]['count'] += row['count'] or 0
    for row in bank_rows:
        bucket = row['bucket']
        key = period_label(bucket, period)
        if key not in merged:
            merged[key] = {'name': key, 'spent': Decimal('0.00'), 'saved': Decimal('0.00'), 'count': 0, 'sort': bucket}
        merged[key]['spent'] += abs(row['spent'] or Decimal('0.00'))
        merged[key]['count'] += row['count'] or 0
    rows = sorted(merged.values(), key=lambda item: item['sort'])[-12:]
    return [{'name': row['name'], 'spent': decimal_value(row['spent']), 'saved': decimal_value(row['saved']), 'count': row['count']} for row in rows]


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
    return Response({'user_id': request.user.id, 'username': request.user.get_username(), 'is_superuser': request.user.is_superuser, 'profile_id': profile.id if profile else None, 'display_name': profile.display_name if profile else '', 'family_id': family.id if family else None, 'family_name': family.name if family else ''})


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
    classified = 0
    for row in parse_bank_csv(file, bank):
        tx = BankTransaction.objects.create(user=request.user, family=family, bank=bank, source_file_name=file.name, **row)
        apply_bank_transaction_classification(tx)
        created += 1
        classified += 1
    for receipt in visible_receipts(request.user).filter(duplicate_of__isnull=True):
        match_bank_transactions_for_receipt(receipt)
    return Response({'created': created, 'classified': classified})


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def summaries(request):
    period = request.query_params.get('period', 'month')
    scope = request.query_params.get('scope', 'family')
    trunc = period_trunc(period)
    qs = visible_receipts(request.user).filter(duplicate_of__isnull=True, purchased_at__isnull=False)
    if scope == 'user':
        qs = qs.filter(user=request.user)
    rows = qs.annotate(period=trunc('purchased_at')).values('period', 'user_id').annotate(spent=Sum('total_amount'), saved=Sum('items__discount_amount')).order_by('-period')
    result = []
    for row in rows:
        result.append({'period': period_label(row['period'], period), 'user_id': row['user_id'], 'spent': decimal_value(row['spent']), 'saved': decimal_value(row['saved'])})
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
    bank_qs = visible_bank_transactions(request.user).filter(matched_receipt__isnull=True, amount__lt=0)
    start = rolling_start(period)
    if start:
        receipts_qs = receipts_qs.filter(purchased_at__gte=start)
        bank_qs = bank_qs.filter(transaction_at__gte=start.date())

    receipt_ids = receipts_qs.values_list('id', flat=True)
    items_qs = ReceiptItem.objects.filter(receipt_id__in=receipt_ids)

    subcategory_qs = items_qs
    unmatched_subcategory_qs = bank_qs
    if category_filter:
        subcategory_qs = subcategory_qs.filter(category=category_filter)
        unmatched_subcategory_qs = unmatched_subcategory_qs.filter(category=category_filter)

    receipt_categories = top_rows(items_qs, 'category', limit=limit)
    bank_categories = bank_top_rows(bank_qs, 'category', limit=limit)
    categories = merge_rows(receipt_categories, bank_categories, limit)

    receipt_subcategories = top_rows(subcategory_qs, 'subcategory', limit=limit)
    bank_subcategories = bank_top_rows(unmatched_subcategory_qs, 'subcategory', limit=limit)
    subcategories = merge_rows(receipt_subcategories, bank_subcategories, limit)

    products = top_rows(items_qs, 'name_normalized', limit=limit)
    receipt_stores = [{'name': row['merchant_name'] or 'Nieznany sklep', 'spent': decimal_value(row['spent']), 'saved': 0.0, 'count': row['count']} for row in receipts_qs.values('merchant_name').annotate(spent=Sum('total_amount'), count=Count('id')).order_by('-spent')[:limit]]
    bank_stores = bank_top_rows(bank_qs, 'merchant_name', limit=limit)
    stores = merge_rows(receipt_stores, bank_stores, limit)

    receipt_spent = items_qs.aggregate(total=Sum('paid_price'))['total'] or Decimal('0.00')
    bank_spent = abs(bank_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))
    saved = items_qs.aggregate(total=Sum('discount_amount'))['total'] or Decimal('0.00')
    all_categories = sorted(set(list(items_qs.exclude(category='').values_list('category', flat=True).distinct()) + list(bank_qs.exclude(category='').values_list('category', flat=True).distinct())))
    timeline = build_timeline(period, items_qs, bank_qs)

    return Response({
        'period': period,
        'category_filter': category_filter,
        'cards': {'spent': decimal_value(receipt_spent + bank_spent), 'saved': decimal_value(saved), 'receipt_count': receipts_qs.count() + bank_qs.count(), 'store_count': len(stores)},
        'available_categories': all_categories,
        'timeline': timeline,
        'categories': categories,
        'subcategories': subcategories,
        'products': products,
        'stores': stores,
    })


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def match_candidates(request):
    qs = MatchCandidate.objects.filter(receipt__in=visible_receipts(request.user), status='needs_review').select_related('receipt', 'bank_transaction').order_by('-score')
    return Response(MatchCandidateSerializer(qs, many=True).data)
