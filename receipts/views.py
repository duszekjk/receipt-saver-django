from datetime import date, datetime, time, timedelta
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .authentication import AppTokenAuthentication
from .bank_parsers import parse_bank_csv
from .models import BankTransaction, MatchCandidate, Receipt
from .openai_bank_transactions import BankClassificationError, classify_bank_statement_rows
from .openai_receipts import ReceiptParseError, ReceiptUnreadableError
from .serializers import MatchCandidateSerializer, ReceiptSerializer
from .services import create_receipt_from_image, match_bank_transactions_for_receipt
from .utils import normalize_text

API_AUTHENTICATION = [AppTokenAuthentication]


def user_family(user):
    profile = getattr(user, 'receipt_profile', None)
    return profile.family if profile and profile.family_id else None


def visible_receipts(user):
    if user.is_superuser:
        return Receipt.objects.all()
    family = user_family(user)
    return Receipt.objects.filter(family=family) if family else Receipt.objects.filter(user=user)


def visible_bank_transactions(user):
    if user.is_superuser:
        return BankTransaction.objects.all()
    family = user_family(user)
    return BankTransaction.objects.filter(family=family) if family else BankTransaction.objects.filter(user=user)


def _month_start(value):
    try:
        return date.fromisoformat(f'{value}-01')
    except (TypeError, ValueError):
        return timezone.localdate().replace(day=1)


def _period_bounds(period, month=''):
    today = timezone.localdate()
    if period == 'month':
        start = _month_start(month)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif period == 'quarter':
        start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        end = date(start.year + (1 if start.month == 10 else 0), 1 if start.month == 10 else start.month + 3, 1)
    elif period == 'year':
        start, end = date(today.year, 1, 1), date(today.year + 1, 1, 1)
    elif period == '30d':
        start, end = today - timedelta(days=29), today + timedelta(days=1)
    elif period == '90d':
        start, end = today - timedelta(days=89), today + timedelta(days=1)
    else:
        start, end = today.replace(day=1), today + timedelta(days=1)
    return start, end


def _aware_bounds(start, end):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(start, time.min), tz), timezone.make_aware(datetime.combine(end, time.min), tz)


def _expense_rows(user, start, end):
    receipt_start, receipt_end = _aware_bounds(start, end)
    receipt_items = []
    for receipt in visible_receipts(user).filter(duplicate_of__isnull=True, purchased_at__gte=receipt_start, purchased_at__lt=receipt_end).prefetch_related('items'):
        for item in receipt.items.all():
            receipt_items.append({'name': item.name, 'merchant': receipt.merchant_name, 'category': item.category or 'Bez kategorii', 'subcategory': item.subcategory or 'Bez podkategorii', 'spent': float(item.paid_price or 0), 'saved': float(item.discount_amount or 0)})
    standalone = []
    for tx in visible_bank_transactions(user).filter(booked_at__gte=start, booked_at__lt=end, matched_receipt__isnull=True, amount__lt=0).exclude(transaction_type__in=['internal_transfer', 'neutral']):
        standalone.append({'name': tx.corrected_description or tx.merchant_name or tx.raw_description, 'merchant': tx.merchant_name or tx.corrected_description or 'Transakcja bankowa', 'category': tx.category or 'Bez kategorii', 'subcategory': tx.subcategory or 'Bez podkategorii', 'spent': float(abs(tx.amount)), 'saved': 0.0})
    return receipt_items + standalone


def _group(rows, key, limit=None):
    grouped = {}
    for row in rows:
        name = row[key]
        current = grouped.setdefault(name, {'name': name, 'spent': 0.0, 'saved': 0.0, 'count': 0})
        current['spent'] += row['spent']
        current['saved'] += row['saved']
        current['count'] += 1
    result = sorted(grouped.values(), key=lambda row: row['spent'], reverse=True)
    return result[:limit] if limit else result


def _get_visible_receipt(user, receipt_id):
    return visible_receipts(user).filter(id=receipt_id).first()


def _parse_manual_datetime(value):
    parsed = parse_datetime(value or '')
    if not parsed:
        try:
            parsed = datetime.combine(date.fromisoformat(value or ''), time.min)
        except (TypeError, ValueError):
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def dashboard(request):
    period = request.GET.get('period', 'month')
    month = request.GET.get('month', '')
    category = request.GET.get('category', '')
    limit = max(1, min(int(request.GET.get('limit', 12)), 100))
    start, end = _period_bounds(period, month)
    rows = _expense_rows(request.user, start, end)
    if category:
        rows = [row for row in rows if row['category'] == category]
    receipt_start, receipt_end = _aware_bounds(start, end)
    receipt_qs = visible_receipts(request.user).filter(duplicate_of__isnull=True, purchased_at__gte=receipt_start, purchased_at__lt=receipt_end)
    months = visible_receipts(request.user).filter(purchased_at__isnull=False).annotate(period=TruncMonth('purchased_at')).values_list('period', flat=True).distinct().order_by('-period')
    available_months = [value.strftime('%Y-%m') for value in months if value]
    return Response({'period': period, 'selected_month': start.strftime('%Y-%m') if period == 'month' else '', 'available_months': available_months, 'category_filter': category, 'cards': {'spent': sum(row['spent'] for row in rows), 'saved': sum(row['saved'] for row in rows), 'receipt_count': receipt_qs.count(), 'store_count': receipt_qs.exclude(merchant_name='').values('merchant_normalized').distinct().count()}, 'available_categories': sorted({row['category'] for row in _expense_rows(request.user, start, end)}), 'timeline': [], 'categories': _group(rows, 'category', limit), 'subcategories': _group(rows, 'subcategory', limit), 'products': _group(rows, 'name', limit), 'stores': _group(rows, 'merchant', limit)})


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def dashboard_subcategory_details(request):
    start, end = _period_bounds('month', request.GET.get('month', ''))
    subcategory = request.GET.get('subcategory', '')
    rows = [row for row in _expense_rows(request.user, start, end) if row['subcategory'] == subcategory]
    return Response({'month': start.strftime('%Y-%m'), 'subcategory': subcategory, 'products': _group(rows, 'name'), 'total_spent': sum(row['spent'] for row in rows), 'total_saved': sum(row['saved'] for row in rows)})


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def summaries(request):
    period = request.GET.get('period', 'month')
    qs = visible_receipts(request.user).filter(duplicate_of__isnull=True, purchased_at__isnull=False)
    trunc = TruncYear('purchased_at') if period == 'year' else TruncQuarter('purchased_at') if period == 'quarter' else TruncMonth('purchased_at')
    rows = qs.annotate(period_value=trunc).values('period_value', 'user_id').annotate(spent=Sum('total_amount')).order_by('-period_value')
    return Response([{'period': row['period_value'].isoformat() if row['period_value'] else None, 'user_id': row['user_id'], 'spent': float(row['spent'] or 0), 'saved': 0.0, 'halfyear': None} for row in rows])


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def match_candidates(request):
    qs = MatchCandidate.objects.filter(status='needs_review').select_related('receipt', 'bank_transaction').prefetch_related('receipt__items')
    if not request.user.is_superuser:
        family = user_family(request.user)
        qs = qs.filter(receipt__family=family) if family else qs.filter(receipt__user=request.user)
    return Response(MatchCandidateSerializer(qs.order_by('-score')[:100], many=True).data)


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
        return Response({'detail': 'Brak zdjęcia paragonu.', 'code': 'missing_image'}, status=400)
    try:
        receipt = create_receipt_from_image(request.user, image)
    except ReceiptUnreadableError as error:
        return Response({'detail': str(error), 'code': 'receipt_unreadable'}, status=422)
    except ReceiptParseError as error:
        return Response({'detail': f'Błąd skanowania paragonu: {error}', 'code': 'receipt_scan_failed'}, status=422)
    family = user_family(request.user)
    if family and not receipt.family_id:
        receipt.family = family
        receipt.save(update_fields=['family'])
    payload = ReceiptSerializer(receipt).data
    if not receipt.purchased_at and (receipt.raw_openai_json or {}).get('scan_status') == 'unreadable_date':
        return Response({'detail': (receipt.raw_openai_json or {}).get('scan_error') or 'Data paragonu jest nieczytelna.', 'code': 'receipt_date_unreadable', 'requires_manual_date': True, 'receipt': payload}, status=202)
    return Response(payload)


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def set_receipt_date(request, receipt_id):
    receipt = _get_visible_receipt(request.user, receipt_id)
    if not receipt:
        return Response({'detail': 'Paragon nie istnieje.'}, status=404)
    purchased_at = _parse_manual_datetime(request.data.get('purchased_at'))
    if not purchased_at:
        return Response({'detail': 'Niepoprawna data paragonu.'}, status=400)
    now = timezone.now()
    if purchased_at > now + timedelta(days=1):
        return Response({'detail': 'Data paragonu nie może być z przyszłości.'}, status=400)
    if purchased_at < now - timedelta(days=366):
        return Response({'detail': 'Data paragonu jest starsza niż 12 miesięcy.'}, status=400)
    receipt.purchased_at = purchased_at
    raw = receipt.raw_openai_json or {}
    raw['scan_status'] = 'ok'
    raw['manual_purchased_at'] = purchased_at.isoformat()
    receipt.raw_openai_json = raw
    duplicate = None if receipt.duplicate_of_id else __import__('receipts.services', fromlist=['find_duplicate_receipt']).find_duplicate_receipt(receipt)
    if duplicate:
        receipt.duplicate_of = duplicate
    receipt.save(update_fields=['purchased_at', 'raw_openai_json', 'duplicate_of'])
    match_bank_transactions_for_receipt(receipt)
    return Response(ReceiptSerializer(receipt).data)


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
            classifications = classify_bank_statement_rows(bank, parsed_rows)
            created = 0
            for row, data in zip(parsed_rows, classifications):
                tx = BankTransaction.objects.create(user=request.user, family=family, bank=bank, source_file_name=file.name, **row)
                tx.corrected_description = data.get('corrected_description') or tx.raw_description or tx.merchant_name or ''
                tx.merchant_name = tx.merchant_name or data.get('merchant_name') or ''
                tx.merchant_normalized = normalize_text(tx.merchant_name)
                tx.transaction_type = data.get('transaction_type') or ''
                tx.category = data.get('category') or ''
                tx.subcategory = data.get('subcategory') or ''
                tx.classification_source = 'openai'
                tx.raw_classification_json = data
                tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
                created += 1
            for receipt in visible_receipts(request.user).filter(duplicate_of__isnull=True):
                match_bank_transactions_for_receipt(receipt)
        return Response({'created': created, 'classified': len(classifications)})
    except BankClassificationError as error:
        return Response({'detail': f'Nie udało się sklasyfikować całego wyciągu: {error}'}, status=422)
