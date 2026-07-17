import secrets
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone


class Family(models.Model):
    name = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'families'

    def __str__(self):
        return self.name


class ReceiptUserProfile(models.Model):
    ROLE_MEMBER = 'member'
    ROLE_MANAGER = 'manager'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name='receipt_profile',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    public_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    is_guest = models.BooleanField(default=False, db_index=True)
    family = models.ForeignKey(Family, related_name='members', null=True, blank=True, on_delete=models.SET_NULL)
    display_name = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    photo = models.ImageField(upload_to='receipt_profiles/', null=True, blank=True)
    role = models.CharField(max_length=24, choices=[(ROLE_MEMBER, 'Member'), (ROLE_MANAGER, 'Family manager')], default=ROLE_MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = uuid.uuid4()
        super().save(*args, **kwargs)

    def __str__(self):
        if self.display_name:
            return self.display_name
        if self.user_id:
            return self.user.get_username()
        return f'Gość {str(self.public_id)[:8]}'


class AppLoginToken(models.Model):
    profile = models.ForeignKey(ReceiptUserProfile, related_name='app_tokens', on_delete=models.CASCADE)
    device_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=160, blank=True)
    secret_key = models.CharField(max_length=128, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def generate_secret():
        return secrets.token_urlsafe(64)

    @classmethod
    def create_for_profile(cls, profile, name=''):
        return cls.objects.create(profile=profile, name=name, secret_key=cls.generate_secret())

    def save(self, *args, **kwargs):
        if not self.secret_key:
            self.secret_key = self.generate_secret()
        super().save(*args, **kwargs)

    def qr_payload(self):
        return {'type': 'receipt_saver_login', 'device_id': str(self.device_id), 'secret_key': self.secret_key}

    def mark_used(self):
        self.last_used_at = timezone.now()
        self.save(update_fields=['last_used_at'])

    def __str__(self):
        return f'{self.profile} / {self.device_id}'


class AppLoginNonce(models.Model):
    token = models.ForeignKey(AppLoginToken, related_name='nonces', on_delete=models.CASCADE)
    nonce = models.CharField(max_length=128)
    timestamp = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('token', 'nonce')]


class Receipt(models.Model):
    profile = models.ForeignKey(ReceiptUserProfile, related_name='receipts', null=True, blank=True, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    family = models.ForeignKey(Family, null=True, blank=True, on_delete=models.SET_NULL)
    image = models.ImageField(upload_to='receipts/%Y/%m/')
    merchant_name = models.CharField(max_length=255, blank=True)
    merchant_normalized = models.CharField(max_length=255, blank=True, db_index=True)
    receipt_barcode = models.CharField(max_length=128, blank=True, db_index=True)
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
    subcategory = models.CharField(max_length=100, blank=True, db_index=True)


class ProductCycleRule(models.Model):
    profile = models.ForeignKey(ReceiptUserProfile, related_name='product_cycle_rules', on_delete=models.CASCADE)
    product_name = models.CharField(max_length=255)
    product_name_normalized = models.CharField(max_length=255, db_index=True)
    interval_days = models.PositiveIntegerField()
    reminder_before_days = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('profile', 'product_name_normalized')]
        ordering = ['product_name_normalized']

    def __str__(self):
        return f'{self.product_name}: {self.interval_days} dni'


class BankTransaction(models.Model):
    TRANSACTION_EXPENSE = 'expense'
    TRANSACTION_INCOME = 'income'
    TRANSACTION_INTERNAL = 'internal_transfer'
    TRANSACTION_NEUTRAL = 'neutral'

    profile = models.ForeignKey(ReceiptUserProfile, related_name='bank_transactions', null=True, blank=True, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    family = models.ForeignKey(Family, null=True, blank=True, on_delete=models.SET_NULL)
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
    category = models.CharField(max_length=100, blank=True, db_index=True)
    subcategory = models.CharField(max_length=100, blank=True, db_index=True)
    transaction_type = models.CharField(max_length=32, choices=[(TRANSACTION_EXPENSE, 'Expense'), (TRANSACTION_INCOME, 'Income'), (TRANSACTION_INTERNAL, 'Internal transfer'), (TRANSACTION_NEUTRAL, 'Neutral')], blank=True, db_index=True)
    corrected_description = models.TextField(blank=True)
    classification_source = models.CharField(max_length=32, blank=True)
    raw_classification_json = models.JSONField(default=dict, blank=True)
    raw_row = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BankImportJob(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(ReceiptUserProfile, related_name='bank_import_jobs', null=True, blank=True, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    family = models.ForeignKey(Family, null=True, blank=True, on_delete=models.SET_NULL)
    bank = models.CharField(max_length=32, default='unknown')
    source_file = models.FileField(upload_to='bank_imports/%Y/%m/')
    source_file_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=24, choices=[(STATUS_QUEUED, 'Queued'), (STATUS_RUNNING, 'Running'), (STATUS_COMPLETED, 'Completed'), (STATUS_FAILED, 'Failed')], default=STATUS_QUEUED, db_index=True)
    progress_current = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    classified_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.bank} import {self.id} {self.status}'


class MatchCandidate(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE)
    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE)
    score = models.FloatField()
    reason = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=[('auto_matched', 'Auto matched'), ('needs_review', 'Needs review'), ('rejected', 'Rejected')])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('receipt', 'bank_transaction')]


class UndoOperation(models.Model):
    profile = models.ForeignKey(ReceiptUserProfile, related_name='undo_operations', null=True, blank=True, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    family = models.ForeignKey(Family, null=True, blank=True, on_delete=models.CASCADE)
    operation_type = models.CharField(max_length=64)
    label = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    undone_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.label
