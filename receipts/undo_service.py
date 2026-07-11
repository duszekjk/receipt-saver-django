from django.db import transaction
from django.utils import timezone

from .models import BankImportJob, BankTransaction, MatchCandidate, Receipt, ReceiptItem, UndoOperation

UNDO_LIMIT = 5


def operation_scope(user):
    profile = getattr(user, 'receipt_profile', None)
    family = profile.family if profile and profile.family_id else None
    return {'family': family} if family else {'user': user, 'family__isnull': True}


def record_undo(user, operation_type, label, payload):
    profile = getattr(user, 'receipt_profile', None)
    family = profile.family if profile and profile.family_id else None
    operation = UndoOperation.objects.create(
        user=user,
        family=family,
        operation_type=operation_type,
        label=label,
        payload=payload,
    )
    stale = list(
        UndoOperation.objects.filter(**operation_scope(user), undone_at__isnull=True)
        .order_by('-created_at', '-id')
        .values_list('id', flat=True)[UNDO_LIMIT:]
    )
    if stale:
        UndoOperation.objects.filter(id__in=stale).delete()
    return operation


def latest_undo(user):
    return UndoOperation.objects.filter(**operation_scope(user), undone_at__isnull=True).first()


def serialize_undo(operation):
    if not operation:
        return {'can_undo': False, 'label': '', 'remaining': 0}
    remaining = UndoOperation.objects.filter(**operation_scope(operation.user), undone_at__isnull=True).count()
    return {
        'can_undo': True,
        'label': operation.label,
        'operation_type': operation.operation_type,
        'remaining': min(remaining, UNDO_LIMIT),
        'created_at': operation.created_at.isoformat(),
    }


def snapshot_receipt(receipt):
    return {
        'id': receipt.id,
        'user_id': receipt.user_id,
        'family_id': receipt.family_id,
        'image': receipt.image.name if receipt.image else '',
        'merchant_name': receipt.merchant_name,
        'merchant_normalized': receipt.merchant_normalized,
        'receipt_barcode': receipt.receipt_barcode,
        'purchased_at': receipt.purchased_at.isoformat() if receipt.purchased_at else None,
        'total_amount': str(receipt.total_amount) if receipt.total_amount is not None else None,
        'currency': receipt.currency,
        'payment_method': receipt.payment_method,
        'content_fingerprint': receipt.content_fingerprint,
        'duplicate_of_id': receipt.duplicate_of_id,
        'raw_openai_json': receipt.raw_openai_json,
        'items': [
            {
                'id': item.id,
                'name': item.name,
                'name_normalized': item.name_normalized,
                'quantity': str(item.quantity) if item.quantity is not None else None,
                'unit_price': str(item.unit_price) if item.unit_price is not None else None,
                'paid_price': str(item.paid_price),
                'regular_price': str(item.regular_price) if item.regular_price is not None else None,
                'discount_amount': str(item.discount_amount),
                'promotion_name': item.promotion_name,
                'is_discounted': item.is_discounted,
                'category': item.category,
                'subcategory': item.subcategory,
            }
            for item in receipt.items.all()
        ],
    }


def restore_receipt(snapshot):
    from django.utils.dateparse import parse_datetime

    fields = dict(snapshot)
    items = fields.pop('items', [])
    fields['purchased_at'] = parse_datetime(fields['purchased_at']) if fields.get('purchased_at') else None
    receipt_id = fields.pop('id')
    receipt, _ = Receipt.objects.update_or_create(id=receipt_id, defaults=fields)
    receipt.items.all().delete()
    for item in items:
        item = dict(item)
        item.pop('id', None)
        ReceiptItem.objects.create(receipt=receipt, **item)
    return receipt


def undo_latest(user):
    with transaction.atomic():
        operation = UndoOperation.objects.select_for_update().filter(
            **operation_scope(user), undone_at__isnull=True
        ).first()
        if not operation:
            return None

        payload = operation.payload or {}
        action = payload.get('action')

        if action == 'delete_receipt':
            receipt = Receipt.objects.filter(id=payload.get('receipt_id')).first()
            if receipt:
                receipt.delete()
        elif action == 'restore_receipt':
            restore_receipt(payload['receipt'])
        elif action == 'delete_bank_import':
            transaction_ids = payload.get('transaction_ids') or []
            if transaction_ids:
                BankTransaction.objects.filter(id__in=transaction_ids).delete()
            job_id = payload.get('job_id')
            if job_id:
                BankImportJob.objects.filter(id=job_id).delete()
        elif action == 'restore_match':
            tx = BankTransaction.objects.select_for_update().filter(id=payload.get('transaction_id')).first()
            if tx:
                tx.matched_receipt_id = payload.get('previous_matched_receipt_id')
                tx.save(update_fields=['matched_receipt'])
            statuses = payload.get('candidate_statuses') or {}
            for candidate_id, status in statuses.items():
                MatchCandidate.objects.filter(id=int(candidate_id)).update(status=status)
        elif action == 'restore_bank_items':
            tx = BankTransaction.objects.select_for_update().filter(id=payload.get('transaction_id')).first()
            if tx:
                tx.raw_classification_json = payload.get('raw_classification_json') or {}
                tx.classification_source = payload.get('classification_source') or ''
                tx.save(update_fields=['raw_classification_json', 'classification_source'])
        else:
            raise ValueError('Ta operacja nie może zostać cofnięta.')

        operation.undone_at = timezone.now()
        operation.save(update_fields=['undone_at'])
        return operation