from collections import Counter
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import BankTransaction, MatchCandidate, Receipt, ReceiptItem
from .openai_receipts import parse_receipt_image
from .utils import build_receipt_fingerprint, money_similarity, normalize_text, text_similarity

MIN_REVIEW_SCORE = 0.75
AUTO_MATCH_SCORE = 0.98
AMOUNT_TOLERANCE = Decimal('0.01')
MIN_MERCHANT_REVIEW_SCORE = 0.35
MIN_MERCHANT_AUTO_SCORE = 0.75
CARD_BANK_MIN_MATCHES = 40
CARD_BANK_MIN_SHARE = 0.90


def user_family(user):
    profile = getattr(user, 'receipt_profile', None)
    return profile.family if profile and profile.family_id else None


def create_receipt_from_image(user, image_file) -> Receipt:
    receipt = Receipt.objects.create(user=user, family=user_family(user), image=image_file)
    try:
        data = parse_receipt_image(receipt.image.path)
    except Exception:
        receipt.delete()
        raise
    data['_ocr_image_path'] = receipt.image.path
    data['_ocr_image_source'] = 'original'
    purchased_at = parse_datetime(data.get('purchased_at') or '')
    if purchased_at and timezone.is_naive(purchased_at):
        purchased_at = timezone.make_aware(purchased_at, timezone.get_current_timezone())
    receipt.merchant_name = data.get('merchant_name') or ''
    receipt.merchant_normalized = normalize_text(receipt.merchant_name)
    receipt.receipt_barcode = data.get('receipt_barcode') or ''
    receipt.purchased_at = purchased_at
    receipt.total_amount = data.get('total_amount')
    receipt.currency = data.get('currency') or 'PLN'
    receipt.payment_method = data.get('payment_method') or ''
    receipt.raw_openai_json = data
    receipt.content_fingerprint = build_receipt_fingerprint(data)
    receipt.save()
    for item in data.get('items', []):
        name = item.get('name') or ''
        ReceiptItem.objects.create(receipt=receipt, name=name, name_normalized=normalize_text(name), quantity=item.get('quantity'), unit_price=item.get('unit_price'), paid_price=item.get('paid_price') or 0, regular_price=item.get('regular_price'), discount_amount=item.get('discount_amount') or 0, promotion_name=item.get('promotion_name') or '', is_discounted=bool(item.get('is_discounted')), category=item.get('category') or '', subcategory=item.get('subcategory') or '')
    duplicate = find_duplicate_receipt(receipt)
    if duplicate:
        receipt.duplicate_of = duplicate
        receipt.save(update_fields=['duplicate_of'])
    if receipt.purchased_at:
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
    qs = Receipt.objects.exclude(id=receipt.id)
    qs = qs.filter(family=receipt.family) if receipt.family_id else qs.filter(user=receipt.user)
    if receipt.receipt_barcode:
        barcode_duplicate = qs.filter(receipt_barcode=receipt.receipt_barcode).order_by('id').first()
        if barcode_duplicate:
            return barcode_duplicate
    qs = qs.filter(total_amount__isnull=False)
    if receipt.purchased_at:
        qs = qs.filter(purchased_at__date__range=[receipt.purchased_at.date() - timedelta(days=1), receipt.purchased_at.date() + timedelta(days=1)])
    best, best_score = None, 0.0
    for candidate in qs[:200]:
        score, _ = receipt_similarity(receipt, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.85 else None


def _amount_exact(receipt, tx):
    return receipt.total_amount is not None and tx.amount is not None and abs(Decimal(receipt.total_amount) - abs(Decimal(tx.amount))) <= AMOUNT_TOLERANCE


def _date_score(receipt, tx):
    if not receipt.purchased_at or not (tx.transaction_at or tx.booked_at):
        return 0.0, None
    delta_days = ((tx.transaction_at or tx.booked_at) - receipt.purchased_at.date()).days
    return (1.0, 0) if delta_days == 0 else ((0.85, 1) if delta_days == 1 else (0.0, delta_days))


def _receipt_card_last4(receipt):
    value = (receipt.raw_openai_json or {}).get('payment_card_last4')
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else ''


def _learned_bank_for_card(receipt):
    card = _receipt_card_last4(receipt)
    if not card:
        return None, 0, 0.0
    receipts = Receipt.objects.filter(raw_openai_json__payment_card_last4=card, banktransaction__isnull=False)
    receipts = receipts.filter(family=receipt.family) if receipt.family_id else receipts.filter(user=receipt.user)
    banks = list(receipts.values_list('banktransaction__bank', flat=True))
    if len(banks) < CARD_BANK_MIN_MATCHES:
        return None, len(banks), 0.0
    counts = Counter(banks)
    bank, count = counts.most_common(1)[0]
    share = count / len(banks)
    return (bank if share >= CARD_BANK_MIN_SHARE else None), len(banks), share


def match_score(receipt, tx):
    base_reason = {'bank_amount': str(tx.amount), 'expense_amount': str(abs(tx.amount)) if tx.amount is not None else '', 'receipt_amount': str(receipt.total_amount), 'bank_transaction_at': str(tx.transaction_at or ''), 'bank_booked_at': str(tx.booked_at or ''), 'receipt_datetime': receipt.purchased_at.isoformat() if receipt.purchased_at else '', 'receipt_merchant': receipt.merchant_name or '', 'bank_merchant': tx.merchant_name or '', 'bank_description': tx.raw_description or '', 'payment_card_last4': _receipt_card_last4(receipt)}
    if tx.amount >= 0:
        return 0.0, {**base_reason, 'ignored': 'income_or_neutral_transaction'}
    if not _amount_exact(receipt, tx):
        return 0.0, {**base_reason, 'ignored': 'amount_not_exact', 'amount_exact': False}
    date_score, delta_days = _date_score(receipt, tx)
    if date_score <= 0:
        return 0.0, {**base_reason, 'ignored': 'date_outside_exact_window', 'amount_exact': True, 'delta_days': delta_days}
    merchant = text_similarity(receipt.merchant_name, tx.merchant_name or tx.raw_description)
    learned_bank, card_matches, card_bank_share = _learned_bank_for_card(receipt)
    card_bank_match = bool(learned_bank and learned_bank == tx.bank)
    if merchant < MIN_MERCHANT_REVIEW_SCORE and not card_bank_match:
        return 0.0, {**base_reason, 'ignored': 'merchant_too_different', 'amount_exact': True, 'delta_days': delta_days, 'merchant': merchant, 'learned_card_bank': learned_bank or '', 'card_history_matches': card_matches, 'card_bank_share': card_bank_share}
    payment = 1.0 if 'kart' in (receipt.payment_method or '').lower() else 0.7
    score = 0.52 + 0.23 * date_score + 0.15 * merchant + 0.05 * payment + (0.05 if card_bank_match else 0.0)
    return score, {**base_reason, 'amount_exact': True, 'date_window': date_score, 'delta_days': delta_days, 'merchant': merchant, 'payment': payment, 'learned_card_bank': learned_bank or '', 'card_history_matches': card_matches, 'card_bank_share': card_bank_share, 'card_bank_match': card_bank_match}


def match_bank_transactions_for_receipt(receipt):
    if not receipt.total_amount or not receipt.purchased_at:
        return []
    start, end = receipt.purchased_at.date(), receipt.purchased_at.date() + timedelta(days=1)
    candidates = BankTransaction.objects.filter(matched_receipt__isnull=True, booked_at__range=[start, end], amount__lt=0)
    candidates = candidates.filter(family=receipt.family) if receipt.family_id else candidates.filter(user=receipt.user)
    results = []
    for tx in candidates:
        score, reason = match_score(receipt, tx)
        if score >= MIN_REVIEW_SCORE:
            status = 'auto_matched' if score >= AUTO_MATCH_SCORE and reason.get('merchant', 0) >= MIN_MERCHANT_AUTO_SCORE and reason.get('delta_days') == 0 else 'needs_review'
            obj, _ = MatchCandidate.objects.update_or_create(receipt=receipt, bank_transaction=tx, defaults={'score': score, 'reason': reason, 'status': status})
            if status == 'auto_matched':
                tx.matched_receipt = receipt
                tx.save(update_fields=['matched_receipt'])
            results.append(obj)
    return results
