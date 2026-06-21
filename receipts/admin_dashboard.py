from decimal import Decimal
from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import redirect, render
from django.urls import path, reverse
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
    return qs


def money(value):
    return value or Decimal('0.00')


def format_money(value):
    return f'{money(value):.2f}'


def percent(part, whole):
    if not whole:
        return 0
    return int((Decimal(part) / Decimal(whole)) * 100)


def merge_chart_rows(primary, secondary, label_key, limit=None):
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
    max_spent = max([row['spent'] for row in rows] + [Decimal('1.00')])
    for row in rows:
        row['bar_width'] = int((row['spent'] / max_spent) * 100) if max_spent else 0
        row['saved_width'] = min(100, percent(row['saved'], row['spent'])) if row['spent'] else 0
        row['spent_display'] = format_money(row['spent'])
        row['saved_display'] = format_money(row['saved'])
    return rows


def prepare_bar_rows(rows, label_key, limit=None, add_other=False):
    rows = list(rows)
    for row in rows:
        row[label_key] = row[label_key] or 'inne'
        row['spent'] = money(row['spent'])
        row['saved'] = money(row.get('saved'))
    if limit and add_other and len(rows) > limit:
        visible = rows[:limit]
        hidden = rows[limit:]
        visible.append({
            label_key: 'pozostale',
            'spent': sum((row['spent'] for row in hidden), Decimal('0.00')),
            'saved': sum((row['saved'] for row in hidden), Decimal('0.00')),
            'count': sum((row.get('count') or 0 for row in hidden), 0),
        })
        rows = visible
    max_spent = max([row['spent'] for row in rows] + [Decimal('1.00')])
    for row in rows:
        row['bar_width'] = int((row['spent'] / max_spent) * 100) if max_spent else 0
        row['saved_width'] = min(100, percent(row['saved'], row['spent'])) if row['spent'] else 0
        row['spent_display'] = format_money(row['spent'])
        row['saved_display'] = format_money(row['saved'])
    return rows


def receipts_dashboard(request):
    family = selected_family(request)
    families = visible_families(request.user)
    receipts = family_receipts_queryset(request)
    banks = family_bank_queryset(request)
    receipt_ids = receipts.values_list('id', flat=True)
    subcategory_limit = int(request.GET.get('subcategory_limit') or 12)
    subcategory_limit = max(3, min(50, subcategory_limit))

    receipt_spent = money(receipts.aggregate(total=Sum('total_amount'))['total'])
    saved = money(ReceiptItem.objects.filter(receipt_id__in=receipt_ids).aggregate(total=Sum('discount_amount'))['total'])
    receipt_count = receipts.count()
    item_count = ReceiptItem.objects.filter(receipt_id__in=receipt_ids).count()

    unmatched_transactions = banks.filter(matched_receipt__isnull=True, amount__lt=0)
    bank_spent = abs(money(unmatched_transactions.aggregate(total=Sum('amount'))['total']))
    total_spent = receipt_spent + bank_spent

    duplicate_qs = Receipt.objects.filter(duplicate_of__isnull=False)
    if family:
        duplicate_qs = duplicate_qs.filter(family=family)
    elif not request.user.is_superuser:
        duplicate_qs = duplicate_qs.filter(user=request.user)
    duplicate_count = duplicate_qs.count()

    all_expense_transactions = banks.filter(amount__lt=0)
    unmatched_count = unmatched_transactions.count()
    transaction_count = all_expense_transactions.count()

    pending_matches = MatchCandidate.objects.filter(status='needs_review')
    if family:
        pending_matches = pending_matches.filter(receipt__family=family)
    elif not request.user.is_superuser:
        pending_matches = pending_matches.none()
    pending_match_count = pending_matches.count()

    monthly_rows = list(receipts.filter(purchased_at__isnull=False).annotate(month=TruncMonth('purchased_at')).values('month').annotate(spent=Sum('total_amount'), count=Count('id'), saved=Sum('items__discount_amount')).order_by('-month')[:12])
    monthly_rows = list(reversed(monthly_rows))
    max_spent = max([row['spent'] or Decimal('0.00') for row in monthly_rows] + [Decimal('1.00')])
    for row in monthly_rows:
        row['spent'] = money(row['spent'])
        row['saved'] = money(row['saved'])
        row['bar_width'] = int((row['spent'] / max_spent) * 100) if max_spent else 0
        row['saved_width'] = min(100, percent(row['saved'], row['spent'])) if row['spent'] else 0
        row['spent_display'] = format_money(row['spent'])
        row['saved_display'] = format_money(row['saved'])

    receipt_category_rows = ReceiptItem.objects.filter(receipt_id__in=receipt_ids).values('category').annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id')).order_by('-spent')
    bank_category_rows = unmatched_transactions.values('category').annotate(spent=Sum('amount'), count=Count('id')).order_by('spent')
    bank_category_rows = [{'category': row['category'] or 'inne', 'spent': abs(money(row['spent'])), 'saved': Decimal('0.00'), 'count': row['count']} for row in bank_category_rows]
    category_rows = merge_chart_rows(receipt_category_rows, bank_category_rows, 'category')

    receipt_subcategory_rows = ReceiptItem.objects.filter(receipt_id__in=receipt_ids).values('subcategory').annotate(spent=Sum('paid_price'), saved=Sum('discount_amount'), count=Count('id')).order_by('-spent')
    bank_subcategory_rows = unmatched_transactions.values('subcategory').annotate(spent=Sum('amount'), count=Count('id')).order_by('spent')
    bank_subcategory_rows = [{'subcategory': row['subcategory'] or 'inne', 'spent': abs(money(row['spent'])), 'saved': Decimal('0.00'), 'count': row['count']} for row in bank_subcategory_rows]
    subcategory_rows = merge_chart_rows(prepare_bar_rows(receipt_subcategory_rows, 'subcategory', limit=subcategory_limit, add_other=True), bank_subcategory_rows, 'subcategory', limit=subcategory_limit)

    recent_unmatched_transactions = unmatched_transactions.order_by('-transaction_at', '-booked_at', '-id')[:12]

    context = {
        **admin.site.each_context(request),
        'title': 'Receipts dashboard',
        'families': families,
        'selected_family': family,
        'subcategory_limit': subcategory_limit,
        'spent': total_spent,
        'spent_display': format_money(total_spent),
        'receipt_spent_display': format_money(receipt_spent),
        'bank_spent_display': format_money(bank_spent),
        'saved': saved,
        'saved_display': format_money(saved),
        'savings_rate': min(100, percent(saved, total_spent)) if total_spent else 0,
        'receipt_count': receipt_count,
        'item_count': item_count,
        'unmatched_count': unmatched_count,
        'unmatched_rate': min(100, percent(unmatched_count, transaction_count)) if transaction_count else 0,
        'pending_match_count': pending_match_count,
        'duplicate_count': duplicate_count,
        'problem_cards': [
            {'label': 'Wydatki bankowe bez paragonu', 'value': unmatched_count, 'level': 'warning' if unmatched_count else 'ok', 'hint': 'Te transakcje są liczone jako osobne wydatki i są widoczne poniżej.'},
            {'label': 'Dopasowania do decyzji', 'value': pending_match_count, 'level': 'warning' if pending_match_count else 'ok', 'hint': 'Pary paragon–bank, które wymagają ręcznego zaakceptowania albo odrzucenia.'},
            {'label': 'Duplikaty paragonów', 'value': duplicate_count, 'level': 'warning' if duplicate_count else 'ok', 'hint': 'Paragony oznaczone jako prawdopodobne duplikaty.'},
        ],
        'monthly_rows': monthly_rows,
        'category_rows': category_rows,
        'subcategory_rows': subcategory_rows,
        'recent_receipts': receipts.select_related('user', 'family').order_by('-purchased_at', '-created_at')[:10],
        'recent_unmatched_transactions': recent_unmatched_transactions,
        'pending_matches': pending_matches.select_related('receipt', 'bank_transaction').order_by('-score')[:10],
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
            if family:
                for receipt in Receipt.objects.filter(family=family, duplicate_of__isnull=True):
                    match_bank_transactions_for_receipt(receipt)
            messages.success(request, f'Zaimportowano transakcje: {created}')
            return redirect(reverse('admin:receipts_banktransaction_changelist'))
    else:
        form = BankStatementImportForm()
    return render(request, 'admin/receipts/import_bank_statement.html', {**admin.site.each_context(request), 'title': 'Import wyciągu bankowego', 'form': form, 'selected_family': family})


def install_receipts_admin_dashboard():
    original_get_urls = admin.site.get_urls

    def get_urls():
        custom = [
            path('receipts-dashboard/', admin.site.admin_view(receipts_dashboard), name='receipts-dashboard'),
            path('receipts-import-bank-statement/', admin.site.admin_view(import_bank_statement_admin), name='receipts-import-bank-statement'),
        ]
        return custom + original_get_urls()

    admin.site.get_urls = get_urls
