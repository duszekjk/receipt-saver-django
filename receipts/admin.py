import base64
import io
import json
from django.contrib import admin, messages
from django.utils.html import format_html
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
    list_display = ('id', 'name', 'created_at')
    search_fields = ('name',)


@admin.register(ReceiptUserProfile)
class ReceiptUserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'display_name', 'family', 'role')
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
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ('id', 'family', 'user', 'merchant_name', 'purchased_at', 'total_amount', 'duplicate_of', 'created_at')
    list_filter = ('family', 'user')
    search_fields = ('merchant_name', 'content_fingerprint')
    inlines = [ReceiptItemInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        profile = getattr(request.user, 'receipt_profile', None)
        if profile and profile.family_id:
            return qs.filter(family=profile.family)
        return qs.filter(user=request.user)


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'family', 'user', 'bank', 'booked_at', 'transaction_at', 'amount', 'merchant_name', 'matched_receipt')
    list_filter = ('family', 'user', 'bank')
    search_fields = ('merchant_name', 'raw_description')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        profile = getattr(request.user, 'receipt_profile', None)
        if profile and profile.family_id:
            return qs.filter(family=profile.family)
        return qs.filter(user=request.user)


@admin.register(MatchCandidate)
class MatchCandidateAdmin(admin.ModelAdmin):
    list_display = ('id', 'receipt', 'bank_transaction', 'score', 'status')
    list_filter = ('status',)


@admin.register(AppLoginNonce)
class AppLoginNonceAdmin(admin.ModelAdmin):
    list_display = ('id', 'token', 'nonce', 'timestamp', 'created_at')
    search_fields = ('nonce', 'token__device_id')
