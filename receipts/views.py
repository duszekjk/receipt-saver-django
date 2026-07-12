from datetime import date, datetime, time, timedelta

from django.db.models import Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .authentication import AppTokenAuthentication
from .models import MatchCandidate
from .openai_receipts import ReceiptParseError, ReceiptUnreadableError
from .profile_access import family_for, profile_for, visible_bank_transactions, visible_receipts
from .serializers import MatchCandidateSerializer, ReceiptSerializer
from .services import DuplicateReceiptError, create_receipt_from_image, match_bank_transactions_for_receipt

API_AUTHENTICATION = [AppTokenAuthentication]


def user_family(principal):
    return family_for(principal)


def _aware_bounds(start, end):
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(end, time.min), tz),
    )


def _group(rows, key, limit=None):
    grouped = {}
    for row in rows:
        name = row.get(key) or 'Bez kategorii'
        current = grouped.setdefault(name, {'name': name, 'spent': 0.0, 'saved': 0.0, 'count': 0})
        current['spent'] += float(row.get('spent') or 0)
        current['saved'] += float(row.get('saved') or 0)
        current['count'] += 1
    result = sorted(grouped.values(), key=lambda row: row['spent'], reverse=True)
    return result[:limit] if limit else result


def _group_with_details(rows, key):
    grouped = {}
    for row in rows:
        name = row.get(key) or 'Bez kategorii'
        current = grouped.setdefault(name, {
            'name': name,
            'merchant': '',
            'spent': 0.0,
            'saved': 0.0,
            'count': 0,
            'source': 'mixed',
            'details': [],
        })
        current['spent'] += float(row.get('spent') or 0)
        current['saved'] += float(row.get('saved') or 0)
        current['count'] += 1
        if not current['merchant'] and row.get('merchant'):
            current['merchant'] = row['merchant']
        current['details'].append({
            'name': row.get('name') or '',
            'merchant': row.get('merchant') or '',
            'spent': row.get('spent') or 0.0,
            'saved': row.get('saved') or 0.0,
            'source': row.get('source') or '',
            'date': row.get('date') or '',
            'receipt_id': row.get('receipt_id'),
            'bank_transaction_id': row.get('bank_transaction_id'),
            'quantity': row.get('quantity'),
            'unit_price': row.get('unit_price'),
            'regular_price': row.get('regular_price'),
            'discount_amount': row.get('discount_amount') or 0.0,
            'promotion_name': row.get('promotion_name') or '',
        })
    for row in grouped.values():
        row['details'] = sorted(row['details'], key=lambda item: item.get('date') or '', reverse=True)
    return sorted(grouped.values(), key=lambda row: row['spent'], reverse=True)


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


def _get_visible_receipt(principal, receipt_id):
    return visible_receipts(principal).filter(id=receipt_id).first()


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
    profile = profile_for(request.user)
    family = family_for(request.user)
    django_user = profile.user if profile else None
    return Response({
        'user_id': django_user.id if django_user else None,
        'username': django_user.get_username() if django_user else (profile.display_name if profile else ''),
        'is_superuser': bool(django_user and django_user.is_superuser),
        'profile_id': profile.id if profile else None,
        'profile_public_id': str(profile.public_id) if profile else None,
        'display_name': profile.display_name if profile else '',
        'is_guest': bool(profile and profile.is_guest),
        'family_id': family.id if family else None,
        'family_name': family.name if family else '',
    })


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def scan_receipt(request):
    image = request.FILES.get('image')
    if not image:
        return Response({'detail': 'Brak zdjęcia paragonu.', 'code': 'missing_image'}, status=400)
    try:
        receipt = create_receipt_from_image(request.user, image)
    except DuplicateReceiptError as error:
        return Response({
            'detail': str(error),
            'code': 'receipt_duplicate',
            'duplicate_receipt_id': error.duplicate.id,
        }, status=409)
    except ReceiptUnreadableError as error:
        return Response({'detail': str(error), 'code': 'receipt_unreadable'}, status=422)
    except ReceiptParseError as error:
        return Response({'detail': f'Błąd skanowania paragonu: {error}', 'code': 'receipt_scan_failed'}, status=422)
    payload = ReceiptSerializer(receipt).data
    if not receipt.purchased_at and (receipt.raw_openai_json or {}).get('scan_status') == 'unreadable_date':
        return Response({
            'detail': (receipt.raw_openai_json or {}).get('scan_error') or 'Data paragonu jest nieczytelna.',
            'code': 'receipt_date_unreadable',
            'requires_manual_date': True,
            'receipt': payload,
        }, status=202)
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
    from .services import find_duplicate_receipt
    duplicate = None if receipt.duplicate_of_id else find_duplicate_receipt(receipt)
    if duplicate:
        receipt.duplicate_of = duplicate
    receipt.save(update_fields=['purchased_at', 'raw_openai_json', 'duplicate_of'])
    match_bank_transactions_for_receipt(receipt)
    return Response(ReceiptSerializer(receipt).data)


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def match_candidates(request):
    qs = MatchCandidate.objects.filter(status='needs_review').select_related('receipt', 'bank_transaction').prefetch_related('receipt__items')
    visible_ids = visible_receipts(request.user).values_list('id', flat=True)
    qs = qs.filter(receipt_id__in=visible_ids)
    return Response(MatchCandidateSerializer(qs.order_by('-score')[:100], many=True).data)


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def summaries(request):
    period = request.GET.get('period', 'month')
    qs = visible_receipts(request.user).filter(duplicate_of__isnull=True, purchased_at__isnull=False)
    trunc = TruncYear('purchased_at') if period == 'year' else TruncQuarter('purchased_at') if period == 'quarter' else TruncMonth('purchased_at')
    rows = qs.annotate(period_value=trunc).values('period_value', 'profile_id').annotate(spent=Sum('total_amount')).order_by('-period_value')
    return Response([{
        'period': row['period_value'].isoformat() if row['period_value'] else None,
        'user_id': row['profile_id'],
        'spent': float(row['spent'] or 0),
        'saved': 0.0,
        'halfyear': None,
    } for row in rows])
