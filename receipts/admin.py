import base64
import io
import json
from django.contrib import admin, messages
from django.db.models import Sum
from django.urls import reverse
from django.utils.html import format_html
from .admin_dashboard import install_receipts_admin_dashboard
from .models import (
    AppLoginNonce,
    AppLoginToken,
    BankTransaction,
    Family,
    MatchCandidate,
    Receipt,
    ReceiptItem,
    ReceiptUserProfile,
)


def user_family(user):
    profile = getattr(user, 'receipt_profile', None)
    return profile.family if profile and profile.family_id else None


def family_filtered_queryset(request, qs):
    if request.user.is_superuser:
        return qs
    family = user_family(request.user)
    if family:
        return qs.filter(family=family)
    return qs.filter(user=request.user)


class DefaultFamilyFilterMixin:
    family_filter_name = 'family__id__exact'

    def changelist_view(self, request, extra_context=None):
        family = user_family(request.user)
        if family and self.family_filter_name not in request.GET:
            query = request.GET.copy()
            query[self.family_filter_name] = str(family.id)
            request.GET = query
            request.META['QUERY_STRING'] = query.urlencode()
        return super().changelist_view(request, extra_context=extra_context)


class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 0


class AppLoginTokenInline(admin.TabularInline):
    model = AppLoginToken
    extra = 0
    readonly_fields = ('device_id', 'created_at', 'last_used_at')
    fields = ('name', 'device_id', 'is_active', 'created_at', 'last_used_at')


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'dashboard_link', 'member_count', 'receipt_count', 'family_spent', 'family_saved', 'created_at')
    search_fields = ('name',)

    def dashboard_link(self, obj):
        url = reverse('admin:receipts-dashboard') + f'?family={obj.id}'
        return format_html('<a class="button" href="{}">Dashboard</a>', url)

    def member_count(self, obj):
        return obj.members.count()

    def receipt_count(self, obj):
        return Receipt.objects.filter(family=obj, duplicate_of__isnull=True).count()

    def family_spent(self, obj):
        return Receipt.objects.filter(family=obj, duplicate_of__isnull=True).aggregate(total=Sum('total_amount'))['total'] or 0

    def family_saved(self, obj):
        return ReceiptItem.objects.filter(receipt__family=obj, receipt__duplicate_of__isnull=True).aggregate(total=Sum('discount_amount'))['total'] or 0


@admin.register(ReceiptUserProfile)
class ReceiptUserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'display_name', 'family', 'role', 'profile_spent', 'profile_saved')
    list_filter = ('family', 'role')
    search_fields = ('user__username', 'user__email', 'display_name')
    inlines = [AppLoginTokenInline]
    actions = ['generate_app_login_token']

    @admin.action(description='Generate app QR login token for selected profiles')
    def generate_app_login_token(self, request, queryset):
        created = 0
        for profile in queryset:
            AppLoginToken.create_for_profile(profile, name='iPhone')
            created += 1
        self.message_user(request, f'Created {created} app login token(s). Open App login tokens to scan QR.', messages.SUCCESS)

    def profile_spent(self, obj):
        return Receipt.objects.filter(user=obj.user, duplicate_of__isnull=True).aggregate(total=Sum('total_amount'))['total'] or 0

    def profile_saved(self, obj):
        return ReceiptItem.objects.filter(receipt__user=obj.user, receipt__duplicate_of__isnull=True).aggregate(total=Sum('discount_amount'))['total'] or 0


@admin.register(AppLoginToken)
class AppLoginTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'profile', 'name', 'device_id', 'has_secret', 'is_active', 'created_at', 'last_used_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('profile__user__username', 'profile__display_name', 'device_id', 'name')
    readonly_fields = ('device_id', 'secret_preview', 'qr_code_preview', 'qr_payload_preview', 'created_at', 'last_used_at')
    fields = ('profile', 'name', 'is_active', 'device_id', 'secret_preview', 'qr_code_preview', 'qr_payload_preview', 'created_at', 'last_used_at')
    actions = ['create_new_token_for_selected_profiles', 'fill_missing_secrets']

    def save_model(self, request, obj, form, change):
        if not obj.secret_key:
            obj.secret_key = AppLoginToken.generate_secret()
        super().save_model(request, obj, form, change)

    def has_secret(self, obj):
        return bool(obj.secret_key)
    has_secret.boolean = True

    def secret_preview(self, obj):
        if not obj or not obj.secret_key:
            return 'Save this token first. A secret will be generated automatically.'
        return f'{obj.secret_key[:8]}...{obj.secret_key[-8:]}'

    def qr_payload_text(self, obj):
        return json.dumps(obj.qr_payload(), separators=(',', ':'))

    def qr_code_preview(self, obj):
        if not obj or not obj.secret_key:
            return 'Save this token first. QR will appear after saving.'
        try:
            import qrcode
            image = qrcode.make(self.qr_payload_text(obj))
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
            return format_html('<img alt="QR login token" width="320" height="320" src="data:image/png;base64,{}" />', encoded)
        except Exception as exc:
            return format_html('<p>QR generation failed: {}</p>', exc)

    def qr_payload_preview(self, obj):
        if not obj or not obj.secret_key:
            return 'Save this token first. Payload will appear after saving.'
        return format_html('<pre style="white-space: pre-wrap">{}</pre>', json.dumps(obj.qr_payload(), indent=2))

    @admin.action(description='Generate replacement token for selected token profiles')
    def create_new_token_for_selected_profiles(self, request, queryset):
        created = 0
        for token in queryset.select_related('profile'):
            AppLoginToken.create_for_profile(token.profile, name=f'{token.name} replacement'.strip())
            created += 1
        self.message_user(request, f'Created {created} replacement token(s). Open each new token to scan QR payload.', messages.SUCCESS)

    @admin.action(description='Fill missing secrets for selected tokens')
    def fill_missing_secrets(self, request, queryset):
        updated = 0
        for token in queryset:
            if not token.secret_key:
                token.secret_key = AppLoginToken.generate_secret()
                token.save(update_fields=['secret_key'])
                updated += 1
        self.message_user(request, f'Filled {updated} missing secret(s).', messages.SUCCESS)


@admin.register(Receipt)
class ReceiptAdmin(DefaultFamilyFilterMixin, admin.ModelAdmin):
    list_display = ('id', 'family', 'user', 'merchant_name', 'purchased_at', 'total_amount', 'discount_total', 'duplicate_of', 'created_at')
    list_filter = ('family', 'user', 'currency', 'created_at')
    search_fields = ('merchant_name', 'content_fingerprint', 'items__name')
    date_hierarchy = 'purchased_at'
    inlines = [ReceiptItemInline]
    actions = ['mark_as_not_duplicate']

    def get_queryset(self, request):
        return family_filtered_queryset(request, super().get_queryset(request))

    @admin.action(description='Mark selected receipts as not duplicate')
    def mark_as_not_duplicate(self, request, queryset):
        updated = queryset.update(duplicate_of=None)
        self.message_user(request, f'Updated {updated} receipt(s).', messages.SUCCESS)


@admin.register(BankTransaction)
class BankTransactionAdmin(DefaultFamilyFilterMixin, admin.ModelAdmin):
    list_display = ('id', 'family', 'user', 'bank', 'booked_at', 'transaction_at', 'amount', 'merchant_name', 'matched_receipt')
    list_filter = ('family', 'user', 'bank', 'booked_at')
    search_fields = ('merchant_name', 'raw_description')
    date_hierarchy = 'booked_at'

    def get_queryset(self, request):
        return family_filtered_queryset(request, super().get_queryset(request))


@admin.register(MatchCandidate)
class MatchCandidateAdmin(admin.ModelAdmin):
    list_display = ('id', 'family', 'receipt', 'bank_transaction', 'score', 'status')
    list_filter = ('receipt__family', 'status')
    actions = ['accept_matches', 'reject_matches']

    def family(self, obj):
        return obj.receipt.family

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('receipt', 'bank_transaction')
        if request.user.is_superuser:
            return qs
        family = user_family(request.user)
        if family:
            return qs.filter(receipt__family=family)
        return qs.filter(receipt__user=request.user)

    @admin.action(description='Accept selected matches')
    def accept_matches(self, request, queryset):
        updated = 0
        for match in queryset.select_related('bank_transaction', 'receipt'):
            match.bank_transaction.matched_receipt = match.receipt
            match.bank_transaction.save(update_fields=['matched_receipt'])
            match.status = 'auto_matched'
            match.save(update_fields=['status'])
            updated += 1
        self.message_user(request, f'Accepted {updated} match(es).', messages.SUCCESS)

    @admin.action(description='Reject selected matches')
    def reject_matches(self, request, queryset):
        updated = queryset.update(status='rejected')
        self.message_user(request, f'Rejected {updated} match(es).', messages.SUCCESS)


@admin.register(AppLoginNonce)
class AppLoginNonceAdmin(admin.ModelAdmin):
    list_display = ('id', 'token', 'nonce', 'timestamp', 'created_at')
    search_fields = ('nonce', 'token__device_id')


install_receipts_admin_dashboard()
