from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('receipts', '0007_bankimportjob'),
    ]

    operations = [
        migrations.AddField(
            model_name='receipt',
            name='receipt_barcode',
            field=models.CharField(blank=True, db_index=True, max_length=128),
        ),
    ]
