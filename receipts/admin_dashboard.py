from decimal import Decimal
from django.contrib import admin
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import render
from django.urls import path
from .models import BankTransaction, Family, MatchCandidate, Receipt, ReceiptItem


def user_family(user):
    profile = getattr(user, 'receipt_profile', None)
    return profile.family if profile and profile.family_id else None


def visible_families(user):
    if user.is_superuser:
        return Family.objects.all().order_by('name')
    family = user_family(user)
    return Family.objects.filter(id=family.id) if family else Family.objects.none()


def selected_family(request):
    families = visible_families(request.user)
    requested = request.GET.get('family')
    if requested and request.user.is_superuser:
        family = families.filter(id=requested).first()
        if family:
            return family
    return user_family(request.user) or families.first()


def family_receipts_queryset(request):
    family = selected_family(request)
    qs = Receipt.objects.filter(duplicate_of__isnull=True)
    if family:
        qs = qs.filter(family=family)
    elif not request.user.is_superuser:
        qs = qs.none()
    return qs


def money(value):
    return value or Decimal('0.00')


def receipts_dashboard(request):
    family = selected_family(request)
    families = visible_families(request.user)
    receipts = family_receipts_queryset(request)
    receipt_ids = receipts.values_list('id', flat=True)

    spent = money(receipts.aggregate(total=Sum('total_amount'))['total'])
    saved = money(ReceiptItem.objects.filter(receipt_id__in=receipt_ids).aggregate(total=Sum('discount_amount'))['total'])
    receipt_count = receipts.count()
    item_count = ReceiptItem.objects.filter(receipt_id__in=receipt_ids).count()
    unmatched_transactions = BankTransaction.objects.filter(matched_receipt__isnull=True)
    if family:
        unmatched_transactions = unmatched_transactions.filter(family=family)
    elif not request.user.is_superuser:
        unmatched_transactions = unmatched_transactions.none()

    monthly_rows = list(
        receipts.filter(purchased_at__isnull=False)
        .annotate(month=TruncMonth('purchased_at'))
        .values('month')
        .annotate(spent=Sum('total_amount'), count=Count('id'), saved=Sum('items__discount_amount'))
        .order_by('-month')[:12]
    )
    monthly_rows = list(reversed(monthly_rows))
    max_spent = max([row['spent'] or Decimal('0.00') for row in monthly_rows] + [Decimal('1.00')])
    for row in monthly_rows:
        row['spent'] = money(row['spent'])
        row['saved'] = money(row['saved'])
        row['bar_width'] = int((row['spent'] / max_spent) * 100) if max_spent else 0

    category_rows = list(
        ReceiptItem.objects.filter(receipt_id__in=receipt_ids)
        .values('category')
        .annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id'))
        .order_by('-spent')[:12]
    )
    max_category = max([row['spent'] or Decimal('0.00') for row in category_rows] + [Decimal('1.00')])
    for row in category_rows:
        row['category'] = row['category'] or 'inne'
        row['spent'] = money(row['spent'])
        row['saved'] = money(row['saved'])
        row['bar_width'] = int((row['spent'] / max_category) * 100) if max_category else 0

    recent_receipts = receipts.select_related('user', 'family').order_by('-purchased_at', '-created_at')[:10]
    pending_matches = MatchCandidate.objects.filter(status='needs_review')
    if family:
        pending_matches = pending_matches.filter(receipt__family=family)
    elif not request.user.is_superuser:
        pending_matches = pending_matches.none()

    context = {
        **admin.site.each_context(request),
        'title': 'Receipts dashboard',
        'families': families,
        'selected_family': family,
        'spent': spent,
        'saved': saved,
        'receipt_count': receipt_count,
        'item_count': item_count,
        'unmatched_count': unmatched_transactions.count(),
        'pending_match_count': pending_matches.count(),
        'monthly_rows': monthly_rows,
        'category_rows': category_rows,
        'recent_receipts': recent_receipts,
        'pending_matches': pending_matches.select_related('receipt', 'bank_transaction').order_by('-score')[:10],
    }
    return render(request, 'admin/receipts/dashboard.html', context)


def install_receipts_admin_dashboard():
    original_get_urls = admin.site.get_urls

    def get_urls():
        custom = [
            path('receipts-dashboard/', admin.site.admin_view(receipts_dashboard), name='receipts-dashboard'),
        ]
        return custom + original_get_urls()

    admin.site.get_urls = get_urls
