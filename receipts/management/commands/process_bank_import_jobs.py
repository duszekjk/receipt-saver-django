import time
from django.core.management.base import BaseCommand
from django.db import transaction
from receipts.bank_import_jobs import process_bank_import_job
from receipts.models import BankImportJob


class Command(BaseCommand):
    help = 'Process queued bank import jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process queued jobs once and exit.')
        parser.add_argument('--sleep', type=float, default=2.0, help='Sleep time between polling rounds.')

    def handle(self, *args, **options):
        while True:
            processed = 0
            while True:
                with transaction.atomic():
                    job = BankImportJob.objects.select_for_update(skip_locked=True).filter(status=BankImportJob.STATUS_QUEUED).order_by('created_at').first()
                    if not job:
                        break
                    job.status = BankImportJob.STATUS_RUNNING
                    job.save(update_fields=['status', 'updated_at'])
                    job_id = job.id
                process_bank_import_job(job_id)
                processed += 1

            if options['once']:
                self.stdout.write(f'Processed {processed} bank import jobs.')
                return
            time.sleep(options['sleep'])
