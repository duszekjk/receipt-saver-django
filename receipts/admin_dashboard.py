from datetime import date, datetime, timedelta
from decimal import Decimal
from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
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


def family_bank_queryset(request):
    family = selected_family(request)
    qs = BankTransaction.objects.all()
    if family:
        qs = qs.filter(family=family)
    elif not request.user.is_superuser:
        qs = qs.none()
    return qs.exclude(transaction_type='internal_transfer')


def parse_month(value):
    try:
        return datetime.strptime(value or '', '%Y-%m').date().replace(day=1)
    except ValueError:
        return None


def next_month(value):
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def month_key(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime('%Y-%m')
    return ''


def available_months_for(receipts, banks):
    receipt_months = receipts.filter(purchased_at__isnull=False).annotate(month=TruncMonth('purchased_at')).values_list('month', flat=True).distinct()
    bank_months = banks.filter(transaction_at__isnull=False).annotate(month=TruncMonth('transaction_at')).values_list('month', flat=True).distinct()
    return sorted({month_key(value) for value in list(receipt_months) + list(bank_months) if value}, reverse=True)


def default_month(months):
    if not months:
        now = timezone.localdate()
        previous = now.replace(day=1) - timedelta(days=1)
        return previous.strftime('%Y-%m')
    now_key = timezone.localdate().strftime('%Y-%m')
    older = [month for month in months if month < now_key]
    return older[0] if older else months[0]


def month_label(value):
    names = {
        '01': 'styczeń', '02': 'luty', '03': 'marzec', '04': 'kwiecień',
        '05': 'maj', '06': 'czerwiec', '07': 'lipiec', '08': 'sierpień',
        '09': 'wrzesień', '10': 'październik', '11': 'listopad', '12': 'grudzień'
    }
    try:
        year, month = value.split('-')
        return f'{names.get(month, month)} {year}'
    except ValueError:
        return value


def money(value):
    return value or Decimal('0.00')


def format_money(value):
    return f'{money(value):.2f}'


def percent(part, whole):
    if not whole:
        return 0
    return int((Decimal(part) / Decimal(whole)) * 100)


def attach_bars(rows, amount_key='spent'):
    rows = list(rows)
    max_value = max([money(row.get(amount_key)) for row in rows] + [Decimal('1.00')])
    for row in rows:
        amount = money(row.get(amount_key))
        row['bar_width'] = int((amount / max_value) * 100) if max_value else 0
        row['spent_display'] = format_money(amount)
        row['saved_display'] = format_money(row.get('saved'))
    return rows


def merge_rows(primary, secondary, label_key, limit=None):
    merged = {}
    for row in list(primary) + list(secondary):
        label = row.get(label_key) or 'inne'
        if label not in merged:
            merged[label] = {label_key: label, 'spent': Decimal('0.00'), 'saved': Decimal('0.00'), 'count': 0}
        merged[label]['spent'] += money(row.get('spent'))
        merged[label]['saved'] += money(row.get('saved'))
        merged[label]['count'] += row.get('count') or 0
    rows = sorted(merged.values(), key=lambda item: item['spent'], reverse=True)
    if limit:
        rows = rows[:limit]
    return attach_bars(rows)


def filter_to_month(receipts, banks, selected_month):
    start = parse_month(selected_month)
    if not start:
        return receipts.none(), banks.none()
    end = next_month(start)
    tz = timezone.get_current_timezone()
    receipt_start = timezone.make_aware(datetime.combine(start, datetime.min.time()), tz)
    receipt_end = timezone.make_aware(datetime.combine(end, datetime.min.time()), tz)
    return receipts.filter(purchased_at__gte=receipt_start, purchased_at__lt=receipt_end), banks.filter(transaction_at__gte=start, transaction_at__lt=end)


def receipts_dashboard(request):
    family = selected_family(request)
    families = visible_families(request.user)
    all_receipts = family_receipts_queryset(request)
    all_banks = family_bank_queryset(request)
    available_months = available_months_for(all_receipts, all_banks)
    selected_month = request.GET.get('month') or default_month(available_months)
    receipts, banks = filter_to_month(all_receipts, all_banks, selected_month)

    subcategory_limit = int(request.GET.get('subcategory_limit') or 12)
    subcategory_limit = max(3, min(50, subcategory_limit))
    selected_category = request.GET.get('category') or ''

    receipt_ids = receipts.values_list('id', flat=True)
    receipt_items = ReceiptItem.objects.filter(receipt_id__in=receipt_ids)
    standalone_expenses = banks.filter(matched_receipt__isnull=True, amount__lt=0)
    incomes = banks.filter(amount__gt=0).exclude(transaction_type='internal_transfer')

    receipt_spent = money(receipt_items.aggregate(total=Sum('paid_price'))['total'])
    bank_spent = abs(money(standalone_expenses.aggregate(total=Sum('amount'))['total']))
    total_spent = receipt_spent + bank_spent
    total_income = money(incomes.aggregate(total=Sum('amount'))['total'])
    balance = total_income - total_spent
    saved = money(receipt_items.aggregate(total=Sum('discount_amount'))['total'])

    receipt_category_rows = receipt_items.values('category').annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id')).order_by('-spent')
    bank_category_rows = standalone_expenses.values('category').annotate(spent=Sum('amount'), count=Count('id')).order_by('spent')
    bank_category_rows = [{'category': row['category'] or 'inne', 'spent': abs(money(row['spent'])), 'saved': Decimal('0.00'), 'count': row['count']} for row in bank_category_rows]
    category_rows = merge_rows(receipt_category_rows, bank_category_rows, 'category')

    sub_receipt_items = receipt_items
    sub_bank_expenses = standalone_expenses
    if selected_category:
        sub_receipt_items = sub_receipt_items.filter(category=selected_category)
        sub_bank_expenses = sub_bank_expenses.filter(category=selected_category)
    receipt_subcategory_rows = sub_receipt_items.values('subcategory').annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id')).order_by('-spent')
    bank_subcategory_rows = sub_bank_expenses.values('subcategory').annotate(spent=Sum('amount'), count=Count('id')).order_by('spent')
    bank_subcategory_rows = [{'subcategory': row['subcategory'] or 'inne', 'spent': abs(money(row['spent'])), 'saved': Decimal('0.00'), 'count': row['count']} for row in bank_subcategory_rows]
    subcategory_rows = merge_rows(receipt_subcategory_rows, bank_subcategory_rows, 'subcategory', limit=subcategory_limit)

    product_rows = attach_bars(receipt_items.values('name').annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id')).order_by('-spent')[:subcategory_limit])
    merchant_receipt_rows = receipts.values('merchant_name').annotate(spent=Sum('total_amount'), count=Count('id')).order_by('-spent')
    merchant_bank_rows = standalone_expenses.values('merchant_name').annotate(spent=Sum('amount'), count=Count('id')).order_by('spent')
    merchant_bank_rows = [{'merchant_name': row['merchant_name'] or 'inne', 'spent': abs(money(row['spent'])), 'saved': Decimal('0.00'), 'count': row['count']} for row in merchant_bank_rows]
    merchant_rows = merge_rows(merchant_receipt_rows, merchant_bank_rows, 'merchant_name', limit=subcategory_limit)
    available_categories = sorted(set([row.get('category') or 'inne' for row in category_rows]))

    pending_matches = MatchCandidate.objects.filter(status='needs_review')
    if family:
        pending_matches = pending_matches.filter(receipt__family=family)
    elif not request.user.is_superuser:
        pending_matches = pending_matches.none()
    pending_match_count = pending_matches.count()

    duplicate_qs = Receipt.objects.filter(duplicate_of__isnull=False)
    if family:
        duplicate_qs = duplicate_qs.filter(family=family)
    elif not request.user.is_superuser:
        duplicate_qs = duplicate_qs.filter(user=request.user)
    duplicate_count = duplicate_qs.count()
    uncategorized_count = standalone_expenses.filter(category__in=['', 'inne']).count() + receipt_items.filter(category__in=['', 'inne']).count()
    attention_count = pending_match_count + duplicate_count + uncategorized_count

    context = {
        **admin.site.each_context(request),
        'title': 'Dashboard wydatków',
        'families': families,
        'selected_family': family,
        'available_months': [{'value': month, 'label': month_label(month)} for month in available_months],
        'selected_month': selected_month,
        'selected_month_label': month_label(selected_month),
        'subcategory_limit': subcategory_limit,
        'selected_category': selected_category,
        'available_categories': available_categories,
        'spent_display': format_money(total_spent),
        'income_display': format_money(total_income),
        'balance_display': format_money(balance),
        'saved_display': format_money(saved),
        'savings_rate': min(100, percent(saved, total_spent)) if total_spent else 0,
        'attention_count': attention_count,
        'category_rows': category_rows,
        'subcategory_rows': subcategory_rows,
        'product_rows': product_rows,
        'merchant_rows': merchant_rows,
        'recent_expenses': standalone_expenses.order_by('-transaction_at', '-booked_at', '-id')[:8],
        'recent_receipts': receipts.select_related('user', 'family').order_by('-purchased_at', '-created_at')[:8],
        'pending_matches': pending_matches.select_related('receipt', 'bank_transaction').order_by('-score')[:8],
        'technical': {'pending_match_count': pending_match_count, 'duplicate_count': duplicate_count, 'uncategorized_count': uncategorized_count},
    }
    return render(request, 'admin/receipts/dashboard.html', context)


def import_bank_statement_admin(request):
    from .bank_parsers import parse_bank_statement
    from .forms import BankStatementImportForm
    from .openai_bank_transactions import apply_bank_transaction_classification
    from .services import match_bank_transactions_for_receipt

    family = selected_family(request)
    if request.method == 'POST':
        form = BankStatementImportForm(request.POST, request.FILES)
        if form.is_valid():
            bank = form.cleaned_data['bank']
            file_obj = form.cleaned_data['file']
            created = 0
            for row in parse_bank_statement(file_obj, bank):
                tx = BankTransaction.objects.create(user=request.user, family=family, bank=bank, source_file_name=file_obj.name, **row)
                apply_bank_transaction_classification(tx)
                created += 1
            for receipt in family_receipts_queryset(request):
                match_bank_transactions_for_receipt(receipt)
            messages.success(request, f'Zaimportowano transakcje: {created}')
            return redirect(reverse('admin:receipts_dashboard'))
    else:
        form = BankStatementImportForm()
    return render(request, 'admin/receipts/import_bank_statement.html', {**admin.site.each_context(request), 'title': 'Import wyciągu bankowego', 'form': form, 'selected_family': family})


def register_admin_dashboard(admin_site):
    original_get_urls = admin_site.get_urls

    def get_urls():
        return [
            path('receipts-dashboard/', admin_site.admin_view(receipts_dashboard), name='receipts_dashboard'),
            path('receipts-bank-import/', admin_site.admin_view(import_bank_statement_admin), name='receipts_bank_import'),
        ] + original_get_urls()

    admin_site.get_urls = get_urls


def install_receipts_admin_dashboard(admin_site=admin.site):
    register_admin_dashboard(admin_site)
