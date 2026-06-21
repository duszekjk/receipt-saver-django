# Generated manually
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('receipts', '0009_receiptitem_subcategory'),
    ]

    operations = [
        migrations.AddField(
            model_name='banktransaction',
            name='category',
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='subcategory',
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='transaction_type',
            field=models.CharField(blank=True, choices=[('expense', 'Expense'), ('income', 'Income'), ('internal_transfer', 'Internal transfer'), ('neutral', 'Neutral')], db_index=True, max_length=32),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='corrected_description',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='classification_source',
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name='banktransaction',
            name='raw_classification_json',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
