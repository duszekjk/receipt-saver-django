from datetime import date, datetime, time, timedelta

from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .views import (
    _aware_bounds,
    _expense_rows,
    _group,
    _group_with_details,
    visible_receipts,
)


API_AUTHENTICATION = [AppTokenAuthentication]


def _month_start(value):
    try:
        return date.fromisoformat(f'{value}-01')
    except (TypeError, ValueError):
        return timezone.localdate().replace(day=1)


def _parse_date(value):
    try:
        return date.fromisoformat(value or '')
    except (TypeError, ValueError):
        return None


def _period_bounds(period, month='', start_value='', end_value=''):
    today = timezone.localdate()
    aliases = {'last30': '30d', 'last90': '90d', 'current_year': 'year', 'last12': '12m'}
    period = aliases.get(period, period)

    if period == 'month':
        start = _month_start(month)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif period == '30d':
        start, end = today - timedelta(days=29), today + timedelta(days=1)
    elif period == '90d':
        start, end = today - timedelta(days=89), today + timedelta(days=1)
    elif period == 'year':
        start, end = date(today.year, 1, 1), date(today.year + 1, 1, 1)
    elif period == '12m':
        end = today + timedelta(days=1)
        start = date(today.year - 1, today.month, 1)
        if today.day == 1:
            start = date(today.year - 1, today.month, 1)
    elif period == 'custom':
        start = _parse_date(start_value)
        inclusive_end = _parse_date(end_value)
        if not start or not inclusive_end or inclusive_end < start:
            raise ValueError('Niepoprawny zakres dat.')
        end = inclusive_end + timedelta(days=1)
    else:
        start, end = today.replace(day=1), today + timedelta(days=1)
    return period, start, end


def _monthly_divisor(request, start, end):
    try:
        explicit = int(request.GET.get('average_months', '0'))
    except ValueError:
        explicit = 0
    if explicit in (12, 24, 36):
        return explicit
    if request.GET.get('monthly_average') == '1':
        days = max((end - start).days, 1)
        return max(days / 30.4375, 1.0)
    return 1.0


def _scale_rows(rows, divisor):
    if divisor == 1:
        return rows
    scaled = []
    for source in rows:
        row = dict(source)
        row['spent'] = float(row.get('spent') or 0) / divisor
        row['saved'] = float(row.get('saved') or 0) / divisor
        scaled.append(row)
    return scaled


def _range_payload(request):
    period, start, end = _period_bounds(
        request.GET.get('period', 'month'),
        request.GET.get('month', ''),
        request.GET.get('start_date', ''),
        request.GET.get('end_date', ''),
    )
    divisor = _monthly_divisor(request, start, end)
    return period, start, end, divisor


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def dashboard(request):
    try:
        period, start, end, divisor = _range_payload(request)
        limit = max(1, min(int(request.GET.get('limit', 12)), 100))
    except (TypeError, ValueError) as error:
        return Response({'detail': str(error)}, status=400)

    category = request.GET.get('category', '')
    source_rows = _expense_rows(request.user, start, end)
    available_categories = sorted({row['category'] for row in source_rows})
    rows = [row for row in source_rows if not category or row['category'] == category]
    display_rows = _scale_rows(rows, divisor)

    receipt_start, receipt_end = _aware_bounds(start, end)
    receipt_qs = visible_receipts(request.user).filter(
        duplicate_of__isnull=True,
        purchased_at__gte=receipt_start,
        purchased_at__lt=receipt_end,
    )
    months = visible_receipts(request.user).filter(purchased_at__isnull=False).annotate(
        period=TruncMonth('purchased_at')
    ).values_list('period', flat=True).distinct().order_by('-period')
    available_months = [value.strftime('%Y-%m') for value in months if value]

    timeline = []
    if divisor == 1 and period != 'month':
        monthly = {}
        for row in rows:
            raw_date = row.get('date') or ''
            try:
                parsed = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                key = parsed.strftime('%Y-%m')
            except ValueError:
                continue
            current = monthly.setdefault(key, {'name': key, 'spent': 0.0, 'saved': 0.0, 'count': 0})
            current['spent'] += row['spent']
            current['saved'] += row['saved']
            current['count'] += 1
        timeline = [monthly[key] for key in sorted(monthly)]

    receipt_count = receipt_qs.count()
    if divisor != 1:
        receipt_count = int(round(receipt_count / divisor))

    return Response({
        'period': period,
        'selected_month': start.strftime('%Y-%m') if period == 'month' else '',
        'available_months': available_months,
        'category_filter': category,
        'start_date': start.isoformat(),
        'end_date': (end - timedelta(days=1)).isoformat(),
        'monthly_average': divisor != 1,
        'average_divisor': divisor,
        'cards': {
            'spent': sum(row['spent'] for row in display_rows),
            'saved': sum(row['saved'] for row in display_rows),
            'receipt_count': receipt_count,
            'store_count': len({row.get('merchant') for row in rows if row.get('merchant')}),
        },
        'available_categories': available_categories,
        'timeline': timeline,
        'categories': _group(display_rows, 'category', limit),
        'subcategories': _group(display_rows, 'subcategory', limit),
        'products': _group(display_rows, 'name', limit),
        'stores': _group(display_rows, 'merchant', limit),
    })


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def dashboard_subcategory_details(request):
    try:
        period, start, end, divisor = _range_payload(request)
    except ValueError as error:
        return Response({'detail': str(error)}, status=400)
    subcategory = request.GET.get('subcategory', '')
    rows = [row for row in _expense_rows(request.user, start, end) if row['subcategory'] == subcategory]
    display_rows = _scale_rows(rows, divisor)
    products = _group_with_details(display_rows, 'name')
    return Response({
        'month': start.strftime('%Y-%m'),
        'subcategory': subcategory,
        'items': products,
        'products': products,
        'total_spent': sum(row['spent'] for row in display_rows),
        'total_saved': sum(row['saved'] for row in display_rows),
    })
