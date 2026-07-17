from datetime import timedelta
from statistics import median

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .models import ProductCycleRule, ReceiptItem
from .profile_access import profile_for, visible_bank_transactions, visible_receipts
from .utils import normalize_text

API_AUTHENTICATION = [AppTokenAuthentication]


def _profile(request):
    return profile_for(request.user)


def _last_purchases(profile, normalized_name, limit=2):
    return list(
        ReceiptItem.objects.filter(
            receipt__profile=profile,
            name_normalized=normalized_name,
            receipt__duplicate_of__isnull=True,
            receipt__purchased_at__isnull=False,
        )
        .select_related('receipt')
        .order_by('-receipt__purchased_at')[:limit]
    )


def _serialize_rule(rule):
    purchases = _last_purchases(rule.profile, rule.product_name_normalized)
    last_at = purchases[0].receipt.purchased_at if purchases else None
    previous_at = purchases[1].receipt.purchased_at if len(purchases) > 1 else None
    expected_at = last_at + timedelta(days=rule.interval_days) if last_at else None
    reminder_at = expected_at - timedelta(days=rule.reminder_before_days) if expected_at else None
    too_frequent = bool(last_at and previous_at and (last_at - previous_at).days < rule.interval_days)
    return {
        'id': rule.id,
        'product_name': rule.product_name,
        'interval_days': rule.interval_days,
        'reminder_before_days': rule.reminder_before_days,
        'enabled': rule.enabled,
        'last_purchase_at': last_at.isoformat() if last_at else None,
        'expected_next_purchase_at': expected_at.isoformat() if expected_at else None,
        'reminder_at': reminder_at.isoformat() if reminder_at else None,
        'too_frequent': too_frequent,
    }


@api_view(['GET', 'POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def cycle_rules(request):
    profile = _profile(request)
    if not profile:
        return Response({'detail': 'Brak profilu.'}, status=403)

    if request.method == 'GET':
        query = normalize_text(request.GET.get('q', ''))
        rules = ProductCycleRule.objects.filter(profile=profile)
        if query:
            rules = rules.filter(product_name_normalized__contains=query)
        return Response([_serialize_rule(rule) for rule in rules])

    product_name = (request.data.get('product_name') or '').strip()
    normalized = normalize_text(product_name)
    try:
        interval_days = int(request.data.get('interval_days'))
        reminder_before_days = int(request.data.get('reminder_before_days', 1))
    except (TypeError, ValueError):
        return Response({'detail': 'Niepoprawna częstotliwość.'}, status=400)
    if not product_name or not normalized or interval_days < 1 or reminder_before_days < 0:
        return Response({'detail': 'Podaj nazwę produktu i poprawne wartości dni.'}, status=400)
    reminder_before_days = min(reminder_before_days, interval_days)
    rule, _ = ProductCycleRule.objects.update_or_create(
        profile=profile,
        product_name_normalized=normalized,
        defaults={
            'product_name': product_name,
            'interval_days': interval_days,
            'reminder_before_days': reminder_before_days,
            'enabled': bool(request.data.get('enabled', True)),
        },
    )
    return Response(_serialize_rule(rule), status=201)


@api_view(['DELETE'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def delete_cycle_rule(request, rule_id):
    profile = _profile(request)
    deleted, _ = ProductCycleRule.objects.filter(profile=profile, id=rule_id).delete()
    return Response(status=204 if deleted else 404)


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def cycle_suggestion(request):
    product_name = (request.GET.get('product_name') or '').strip()
    normalized = normalize_text(product_name)
    if not normalized:
        return Response({'suggestion': None})
    rows = list(
        ProductCycleRule.objects.filter(product_name_normalized=normalized, enabled=True)
        .values('profile_id', 'interval_days', 'reminder_before_days')
    )
    distinct_profiles = len({row['profile_id'] for row in rows})
    # Nie ujawniamy ustawień pojedynczych użytkowników. Sugestia powstaje dopiero
    # po zebraniu co najmniej trzech niezależnych profili.
    if distinct_profiles < 3:
        return Response({'suggestion': None, 'sample_size': distinct_profiles})
    return Response({
        'suggestion': {
            'interval_days': int(median(row['interval_days'] for row in rows)),
            'reminder_before_days': int(median(row['reminder_before_days'] for row in rows)),
        },
        'sample_size': distinct_profiles,
    })


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def purchase_search(request):
    query = (request.GET.get('q') or '').strip()
    normalized = normalize_text(query)
    if not normalized:
        return Response([])

    receipts = visible_receipts(request.user).filter(duplicate_of__isnull=True)
    receipt_items = (
        ReceiptItem.objects.filter(receipt__in=receipts)
        .filter(Q(name_normalized__contains=normalized) | Q(category__icontains=query) | Q(subcategory__icontains=query))
        .select_related('receipt')
        .order_by('-receipt__purchased_at')[:100]
    )
    results = [{
        'kind': 'receipt_item',
        'id': item.id,
        'name': item.name,
        'merchant': item.receipt.merchant_name,
        'date': item.receipt.purchased_at.isoformat() if item.receipt.purchased_at else None,
        'amount': str(item.paid_price),
        'currency': item.receipt.currency,
        'category': item.category,
        'subcategory': item.subcategory,
        'receipt_id': item.receipt_id,
    } for item in receipt_items]

    bank_rows = visible_bank_transactions(request.user).filter(
        Q(merchant_normalized__contains=normalized)
        | Q(raw_description__icontains=query)
        | Q(corrected_description__icontains=query)
        | Q(category__icontains=query)
        | Q(subcategory__icontains=query)
    ).order_by('-transaction_at', '-booked_at')[:100]
    results.extend({
        'kind': 'bank_transaction',
        'id': row.id,
        'name': row.corrected_description or row.merchant_name or row.raw_description,
        'merchant': row.merchant_name,
        'date': (row.transaction_at or row.booked_at).isoformat() if (row.transaction_at or row.booked_at) else None,
        'amount': str(abs(row.amount)),
        'currency': row.currency,
        'category': row.category,
        'subcategory': row.subcategory,
        'receipt_id': row.matched_receipt_id,
    } for row in bank_rows)
    results.sort(key=lambda row: row.get('date') or '', reverse=True)
    return Response(results[:150])
