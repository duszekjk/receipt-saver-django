from django.db.models import Q

from .views import _aware_bounds, visible_bank_transactions, visible_receipts


def _receipt_item_rows(receipt, effective_date, bank_transaction_id=None):
    merchant = receipt.merchant_name or 'Paragon'
    return [
        {
            'name': item.name,
            'merchant': merchant,
            'category': item.category or 'Bez kategorii',
            'subcategory': item.subcategory or 'Bez podkategorii',
            'spent': float(item.paid_price or 0),
            'saved': float(item.discount_amount or 0),
            'source': 'receipt',
            'date': effective_date.isoformat() if effective_date else '',
            'receipt_id': receipt.id,
            'bank_transaction_id': bank_transaction_id,
            'quantity': float(item.quantity) if item.quantity is not None else None,
            'unit_price': float(item.unit_price) if item.unit_price is not None else None,
            'regular_price': float(item.regular_price) if item.regular_price is not None else None,
            'discount_amount': float(item.discount_amount or 0),
            'promotion_name': item.promotion_name or '',
        }
        for item in receipt.items.all()
    ]


def expense_rows(user, start, end):
    """Return expense rows without double counting matched receipts and bank transactions.

    A confirmed match means that the bank transaction supplies payment metadata only.
    The receipt and its individual items remain the source of product names, amounts and
    categories. For range filtering, a matched receipt may use the bank transaction date,
    so accepting a match cannot make its items disappear because of a slightly different
    or incorrectly scanned receipt date.
    """
    receipt_start, receipt_end = _aware_bounds(start, end)
    rows = []
    included_receipt_ids = set()

    matched_transactions = (
        visible_bank_transactions(user)
        .filter(
            amount__lt=0,
            matched_receipt__isnull=False,
        )
        .exclude(transaction_type__in=['internal_transfer', 'neutral'])
        .filter(
            Q(transaction_at__gte=start, transaction_at__lt=end)
            | Q(transaction_at__isnull=True, booked_at__gte=start, booked_at__lt=end)
        )
        .select_related('matched_receipt')
        .prefetch_related('matched_receipt__items')
    )

    for tx in matched_transactions:
        receipt = tx.matched_receipt
        if not receipt or receipt.duplicate_of_id or receipt.id in included_receipt_ids:
            continue
        effective_date = tx.transaction_at or tx.booked_at or receipt.purchased_at
        rows.extend(_receipt_item_rows(receipt, effective_date, tx.id))
        included_receipt_ids.add(receipt.id)

    dated_receipts = (
        visible_receipts(user)
        .filter(
            duplicate_of__isnull=True,
            purchased_at__gte=receipt_start,
            purchased_at__lt=receipt_end,
        )
        .prefetch_related('items')
    )
    for receipt in dated_receipts:
        if receipt.id in included_receipt_ids:
            continue
        rows.extend(_receipt_item_rows(receipt, receipt.purchased_at))
        included_receipt_ids.add(receipt.id)

    standalone_transactions = (
        visible_bank_transactions(user)
        .filter(
            amount__lt=0,
            matched_receipt__isnull=True,
        )
        .exclude(transaction_type__in=['internal_transfer', 'neutral'])
        .filter(
            Q(transaction_at__gte=start, transaction_at__lt=end)
            | Q(transaction_at__isnull=True, booked_at__gte=start, booked_at__lt=end)
        )
    )

    for tx in standalone_transactions:
        transaction_date = tx.transaction_at or tx.booked_at
        date_value = transaction_date.isoformat() if transaction_date else ''
        merchant = tx.merchant_name or tx.corrected_description or 'Transakcja bankowa'
        manual_items = (tx.raw_classification_json or {}).get('manual_items') or []
        if manual_items:
            for item in manual_items:
                rows.append({
                    'name': item.get('name') or merchant,
                    'merchant': merchant,
                    'category': item.get('category') or tx.category or 'Bez kategorii',
                    'subcategory': item.get('subcategory') or tx.subcategory or 'Bez podkategorii',
                    'spent': float(item.get('amount') or 0),
                    'saved': 0.0,
                    'source': 'bank',
                    'date': date_value,
                    'receipt_id': None,
                    'bank_transaction_id': tx.id,
                    'quantity': None,
                    'unit_price': None,
                    'regular_price': None,
                    'discount_amount': 0.0,
                    'promotion_name': '',
                })
        else:
            rows.append({
                'name': tx.corrected_description or tx.merchant_name or tx.raw_description,
                'merchant': merchant,
                'category': tx.category or 'Bez kategorii',
                'subcategory': tx.subcategory or 'Bez podkategorii',
                'spent': float(abs(tx.amount)),
                'saved': 0.0,
                'source': 'bank',
                'date': date_value,
                'receipt_id': None,
                'bank_transaction_id': tx.id,
                'quantity': None,
                'unit_price': None,
                'regular_price': None,
                'discount_amount': 0.0,
                'promotion_name': '',
            })

    return rows
