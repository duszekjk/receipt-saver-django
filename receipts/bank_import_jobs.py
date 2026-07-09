import logging
from django.db import transaction
from django.utils import timezone
from .bank_parsers import parse_bank_csv
from .models import BankImportJob, BankTransaction
from . import openai_bank_transactions
from .openai_bank_transactions import BankClassificationError
from .services import match_bank_transactions_for_receipt
from .utils import normalize_text
from .views import visible_receipts

logger = logging.getLogger(__name__)

BACKGROUND_IMPORT_TIMEOUT_SECONDS = 60 * 60


def serialize_bank_import_job(job):
    return {
        'job_id': str(job.id),
        'bank': job.bank,
        'status': job.status,
        'progress_current': job.progress_current,
        'progress_total': job.progress_total,
        'created': job.created_count,
        'classified': job.classified_count,
        'error_message': job.error_message,
        'created_at': job.created_at.isoformat() if job.created_at else None,
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
    }


def _mark_failed(job, error):
    job.status = BankImportJob.STATUS_FAILED
    job.error_message = str(error)
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error_message', 'finished_at', 'updated_at'])


def _allow_long_background_openai_calls():
    openai_bank_transactions.BANK_CLASSIFICATION_OVERALL_TIMEOUT_SECONDS = BACKGROUND_IMPORT_TIMEOUT_SECONDS
    openai_bank_transactions.OPENAI_REQUEST_TIMEOUT_SECONDS = BACKGROUND_IMPORT_TIMEOUT_SECONDS


def _transaction_identity_filter(job, row):
    scope = {'user': job.user, 'bank': job.bank}
    if job.family_id:
        scope = {'family': job.family, 'bank': job.bank}
    return {
        **scope,
        'booked_at': row.get('booked_at'),
        'transaction_at': row.get('transaction_at'),
        'amount': row.get('amount'),
        'currency': row.get('currency') or 'PLN',
        'raw_description': row.get('raw_description') or '',
    }


def _remove_existing_exact_duplicates(job):
    qs = BankTransaction.objects.filter(bank=job.bank)
    qs = qs.filter(family=job.family) if job.family_id else qs.filter(user=job.user)
    seen = {}
    duplicate_ids = []
    fields = ('booked_at', 'transaction_at', 'amount', 'currency', 'raw_description')
    for tx in qs.order_by('id'):
        key = tuple(getattr(tx, field) for field in fields)
        if key in seen:
            canonical = seen[key]
            if not canonical.matched_receipt_id and tx.matched_receipt_id:
                canonical.matched_receipt = tx.matched_receipt
                canonical.save(update_fields=['matched_receipt'])
            duplicate_ids.append(tx.id)
        else:
            seen[key] = tx
    if duplicate_ids:
        BankTransaction.objects.filter(id__in=duplicate_ids).delete()
    return len(duplicate_ids)


def process_bank_import_job(job_id):
    job = BankImportJob.objects.select_related('user', 'family').get(id=job_id)
    if job.status not in [BankImportJob.STATUS_QUEUED, BankImportJob.STATUS_RUNNING]:
        return job

    job.status = BankImportJob.STATUS_RUNNING
    job.started_at = job.started_at or timezone.now()
    job.error_message = ''
    job.save(update_fields=['status', 'started_at', 'error_message', 'updated_at'])

    try:
        _allow_long_background_openai_calls()
        with job.source_file.open('rb') as file_obj:
            file_obj.name = job.source_file_name or job.source_file.name
            parsed_rows = list(parse_bank_csv(file_obj, job.bank))
        if not parsed_rows:
            raise BankClassificationError('Nie znaleziono żadnych transakcji w pliku wyciągu.')

        job.progress_total = len(parsed_rows)
        job.progress_current = 0
        job.save(update_fields=['progress_total', 'progress_current', 'updated_at'])

        classifications = openai_bank_transactions.classify_bank_statement_rows(job.bank, parsed_rows)
        if len(classifications) != len(parsed_rows):
            raise BankClassificationError('Liczba klasyfikacji nie zgadza się z liczbą transakcji.')

        with transaction.atomic():
            _remove_existing_exact_duplicates(job)
            created = 0
            classified = 0
            for row, data in zip(parsed_rows, classifications):
                existing = BankTransaction.objects.filter(**_transaction_identity_filter(job, row)).order_by('id').first()
                if existing:
                    continue

                tx = BankTransaction.objects.create(
                    user=job.user,
                    family=job.family,
                    bank=job.bank,
                    source_file_name=job.source_file_name,
                    **row,
                )
                tx.corrected_description = data.get('corrected_description') or tx.raw_description or tx.merchant_name or ''
                tx.merchant_name = tx.merchant_name or data.get('merchant_name') or ''
                tx.merchant_normalized = normalize_text(tx.merchant_name)
                tx.transaction_type = data.get('transaction_type') or ''
                tx.category = data.get('category') or ''
                tx.subcategory = data.get('subcategory') or ''
                tx.classification_source = 'openai'
                tx.raw_classification_json = data
                tx.save(update_fields=['corrected_description', 'merchant_name', 'merchant_normalized', 'transaction_type', 'category', 'subcategory', 'classification_source', 'raw_classification_json'])
                created += 1
                classified += 1

            for receipt in visible_receipts(job.user).filter(duplicate_of__isnull=True):
                match_bank_transactions_for_receipt(receipt)

            job.status = BankImportJob.STATUS_COMPLETED
            job.progress_current = len(parsed_rows)
            job.created_count = created
            job.classified_count = classified
            job.error_message = ''
            job.finished_at = timezone.now()
            job.save(update_fields=['status', 'progress_current', 'created_count', 'classified_count', 'error_message', 'finished_at', 'updated_at'])
    except Exception as error:
        logger.exception('Bank import job failed: %s', job.id)
        _mark_failed(job, error)
    return job
