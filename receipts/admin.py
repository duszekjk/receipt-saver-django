from django.contrib import admin
from .models import BankTransaction, MatchCandidate, Receipt, ReceiptItem


class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 0


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ('id', 'merchant_name', 'purchased_at', 'total_amount', 'duplicate_of', 'created_at')
    search_fields = ('merchant_name', 'content_fingerprint')
    inlines = [ReceiptItemInline]


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'bank', 'booked_at', 'transaction_at', 'amount', 'merchant_name', 'matched_receipt')
    search_fields = ('merchant_name', 'raw_description')


@admin.register(MatchCandidate)
class MatchCandidateAdmin(admin.ModelAdmin):
    list_display = ('id', 'receipt', 'bank_transaction', 'score', 'status')
