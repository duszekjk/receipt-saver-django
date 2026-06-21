from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .categories import normalize_category
from .models import BankTransaction, MatchCandidate, Receipt, ReceiptItem
from .openai_receipts import parse_receipt_image
from .utils import build_receipt_fingerprint, money_similarity, normalize_text, text_similarity


MIN_REVIEW_SCORE = 0.40
AUTO_MATCH_SCORE = 0.92


def user_family(user):
    profile = getattr(user, 'receipt_profile', None)
    return profile.family if profile and profile.family_id else None


def create_receipt_from_image(user, image_file) -> Receipt:
    receipt = Receipt.objects.create(user=user, family=user_family(user), image=image_file)
    data = parse_receipt_image(receipt.image.path)
    data['_ocr_image_path'] = receipt.image.path
    data['_ocr_image_source'] = 'original'
    purchased_at = parse_datetime(data.get('purchased_at') or '')
    if purchased_at and timezone.is_naive(purchased_at):
        purchased_at = timezone.make_aware(purchased_at, timezone.get_current_timezone())

    receipt.merchant_name = data.get('merchant_name') or ''
    receipt.merchant_normalized = normalize_text(receipt.merchant_name)
    receipt.purchased_at = purchased_at
    receipt.total_amount = data.get('total_amount')
    receipt.currency = data.get('currency') or 'PLN'
    receipt.payment_method = data.get('payment_method') or ''
    receipt.raw_openai_json = data
    receipt.content_fingerprint = build_receipt_fingerprint(data)
    receipt.save()

    for item in data.get('items', []):
        category, subcategory = normalize_category(item.get('category'), item.get('subcategory'))
        ReceiptItem.objects.create(
            receipt=receipt,
            name=item.get('name') or '',
            name_normalized=normalize_text(item.get('name') or ''),
            quantity=item.get('quantity'),
            unit_price=item.get('unit_price'),
            paid_price=item.get('paid_price') or 0,
            regular_price=item.get('regular_price'),
            discount_amount=item.get('discount_amount') or 0,
            promotion_name=item.get('promotion_name') or '',
            is_discounted=bool(item.get('is_discounted')),
            category=category,
            subcategory=subcategory,
        )

    duplicate = find_duplicate_receipt(receipt)
    if duplicate:
        receipt.duplicate_of = duplicate
        receipt.save(update_fields=['duplicate_of'])
    match_bank_transactions_for_receipt(receipt)
    return receipt


def receipt_similarity(a: Receipt, b: Receipt):
    amount = money_similarity(a.total_amount, b.total_amount, Decimal('0.50'))
    merchant = text_similarity(a.merchant_name, b.merchant_name)
    date_score = 0.0
    if a.purchased_at and b.purchased_at:
        minutes = abs((a.purchased_at - b.purchased_at).total_seconds()) / 60
        date_score = 1.0 if minutes <= 30 else max(0.0, 1.0 - minutes / (24 * 60))
    a_items = ' '.join(a.items.values_list('name_normalized', flat=True))
    b_items = ' '.join(b.items.values_list('name_normalized', flat=True))
    items = text_similarity(a_items, b_items)
    score = 0.30 * amount + 0.20 * date_score + 0.20 * merchant + 0.30 * items
    return score, {'amount': amount, 'date_time': date_score, 'merchant': merchant, 'items': items}


def find_duplicate_receipt(receipt: Receipt):
    qs = Receipt.objects.filter(total_amount__isnull=False).exclude(id=receipt.id)
    if receipt.family_id:
        qs = qs.filter(family=receipt.family)
    else:
        qs = qs.filter(user=receipt.user)
    if receipt.purchased_at:
        qs = qs.filter(purchased_at__date__range=[receipt.purchased_at.date() - timedelta(days=1), receipt.purchased_at.date() + timedelta(days=1)])
    best = None
    best_score = 0.0
    for candidate in qs[:200]:
        score, _ = receipt_similarity(receipt, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.85 else None


def match_score(receipt: Receipt, tx: BankTransaction):
    if tx.amount >= 0:
        return 0.0, {'ignored': 'income_or_neutral_transaction'}
    amount = money_similarity(receipt.total_amount, abs(tx.amount), Decimal('0.50'))
    date_score = 0.0
    if receipt.purchased_at and (tx.transaction_at or tx.booked_at):
        bank_date = tx.transaction_at or tx.booked_at
        delta_days = (bank_date - receipt.purchased_at.date()).days
        if -2 <= delta_days <= 10:
            date_score = 1.0 - min(abs(delta_days), 10) / 10.0
    merchant = text_similarity(receipt.merchant_name, tx.merchant_name or tx.raw_description)
    payment = 1.0 if 'kart' in (receipt.payment_method or '').lower() else 0.7
    score = 0.60 * amount + 0.25 * date_score + 0.10 * merchant + 0.05 * payment
    return score, {'amount': amount, 'date_window': date_score, 'merchant': merchant, 'payment': payment, 'bank_amount': str(tx.amount), 'expense_amount': str(abs(tx.amount))}


def match_bank_transactions_for_receipt(receipt: Receipt):
    if not receipt.total_amount or not receipt.purchased_at:
        return []
    start = receipt.purchased_at.date() - timedelta(days=2)
    end = receipt.purchased_at.date() + timedelta(days=10)
    candidates = BankTransaction.objects.filter(matched_receipt__isnull=True, booked_at__range=[start, end], amount__lt=0)
    if receipt.family_id:
        candidates = candidates.filter(family=receipt.family)
    else:
        candidates = candidates.filter(user=receipt.user)
    results = []
    for tx in candidates:
        score, reason = match_score(receipt, tx)
        if score >= MIN_REVIEW_SCORE:
            status = 'auto_matched' if score >= AUTO_MATCH_SCORE else 'needs_review'
            obj, _ = MatchCandidate.objects.update_or_create(receipt=receipt, bank_transaction=tx, defaults={'score': score, 'reason': reason, 'status': status})
            if status == 'auto_matched':
                tx.matched_receipt = receipt
                tx.save(update_fields=['matched_receipt'])
            results.append(obj)
    return results
