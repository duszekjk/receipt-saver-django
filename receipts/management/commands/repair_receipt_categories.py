from django.core.management.base import BaseCommand
from receipts.categories import infer_category_from_name
from receipts.models import ReceiptItem


class Command(BaseCommand):
    help = 'Naprawia oczywiste kategorie pozycji paragonów, szczególnie te, które trafiły do Inne/inne.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        checked = 0
        changed = 0
        qs = ReceiptItem.objects.all().order_by('id')
        for item in qs.iterator():
            checked += 1
            category, subcategory = infer_category_from_name(item.name, item.category, item.subcategory)
            if category != item.category or subcategory != item.subcategory:
                changed += 1
                self.stdout.write(f'{item.id}: {item.name}: {item.category}/{item.subcategory} -> {category}/{subcategory}')
                if not dry_run:
                    item.category = category
                    item.subcategory = subcategory
                    item.save(update_fields=['category', 'subcategory'])
        self.stdout.write(self.style.SUCCESS(f'Sprawdzono: {checked}, zmieniono: {changed}, dry_run={dry_run}'))
