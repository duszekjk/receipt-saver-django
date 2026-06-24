import logging
from django.db import transaction
from django.utils import timezone
from .bank_parsers import parse_bank_csv
from .models import BankImportJob, BankTransaction
from .openai_bank_transactions import BankClassificationError, classify_bank_statement_rows
from .services import match_bank_transactions_for_receipt
from .utils import normalize_text
from .views import visible_receipts

logger = logging.getLogger(__name__)


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


def process_bank_import_job(job_id):
    job = BankImportJob.objects.select_related('user', 'family').get(id=job_id)
    if job.status not in [BankImportJob.STATUS_QUEUED, BankImportJob.STATUS_RUNNING]:
        return job

    job.status = BankImportJob.STATUS_RUNNING
    job.started_at = job.started_at or timezone.now()
    job.error_message = ''
    job.save(update_fields=['status', 'started_at', 'error_message', 'updated_at'])

    try:
        with job.source_file.open('rb') as file_obj:
            file_obj.name = job.source_file_name or job.source_file.name
            parsed_rows = list(parse_bank_csv(file_obj, job.bank))
        if not parsed_rows:
            raise BankClassificationError('Nie znaleziono żadnych transakcji w pliku wyciągu.')

        job.progress_total = len(parsed_rows)
        job.progress_current = 0
        job.save(update_fields=['progress_total', 'progress_current', 'updated_at'])

        classifications = classify_bank_statement_rows(job.bank, parsed_rows)
        if len(classifications) != len(parsed_rows):
            raise BankClassificationError('Liczba klasyfikacji nie zgadza się z liczbą transakcji.')

        with transaction.atomic():
            created = 0
            for row, data in zip(parsed_rows, classifications):
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

            for receipt in visible_receipts(job.user).filter(duplicate_of__isnull=True):
                match_bank_transactions_for_receipt(receipt)

            job.status = BankImportJob.STATUS_COMPLETED
            job.progress_current = len(parsed_rows)
            job.created_count = created
            job.classified_count = len(classifications)
            job.error_message = ''
            job.finished_at = timezone.now()
            job.save(update_fields=['status', 'progress_current', 'created_count', 'classified_count', 'error_message', 'finished_at', 'updated_at'])
    except Exception as error:
        logger.exception('Bank import job failed: %s', job.id)
        _mark_failed(job, error)
    return job
