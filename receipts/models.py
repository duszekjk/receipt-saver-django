from decimal import Decimal
from django.conf import settings
from django.db import models


class Receipt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='receipts/%Y/%m/')
    merchant_name = models.CharField(max_length=255, blank=True)
    merchant_normalized = models.CharField(max_length=255, blank=True, db_index=True)
    purchased_at = models.DateTimeField(null=True, blank=True, db_index=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, default='PLN')
    payment_method = models.CharField(max_length=64, blank=True)
    content_fingerprint = models.CharField(max_length=512, blank=True, db_index=True)
    duplicate_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL)
    raw_openai_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def discount_total(self):
        return sum((item.discount_amount or Decimal('0.00')) for item in self.items.all())

    def __str__(self):
        return f'{self.merchant_name} {self.total_amount} {self.purchased_at}'


class ReceiptItem(models.Model):
    receipt = models.ForeignKey(Receipt, related_name='items', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    name_normalized = models.CharField(max_length=255, blank=True, db_index=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    paid_price = models.DecimalField(max_digits=12, decimal_places=2)
    regular_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    promotion_name = models.CharField(max_length=255, blank=True)
    is_discounted = models.BooleanField(default=False)
    category = models.CharField(max_length=100, blank=True, db_index=True)


class BankTransaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    bank = models.CharField(max_length=32, default='unknown')
    booked_at = models.DateField(null=True, blank=True, db_index=True)
    transaction_at = models.DateField(null=True, blank=True, db_index=True)
    merchant_name = models.CharField(max_length=255, blank=True)
    merchant_normalized = models.CharField(max_length=255, blank=True, db_index=True)
    raw_description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, db_index=True)
    currency = models.CharField(max_length=8, default='PLN')
    source_file_name = models.CharField(max_length=255, blank=True)
    matched_receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.SET_NULL)
    raw_row = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MatchCandidate(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE)
    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE)
    score = models.FloatField()
    reason = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=[
        ('auto_matched', 'Auto matched'),
        ('needs_review', 'Needs review'),
        ('rejected', 'Rejected'),
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('receipt', 'bank_transaction')]
