from dataclasses import dataclass

from django.db.models import Q

from .models import BankImportJob, BankTransaction, Receipt, ReceiptUserProfile, UndoOperation


@dataclass
class ProfilePrincipal:
    receipt_profile: ReceiptUserProfile

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_superuser(self):
        return False

    @property
    def id(self):
        return None

    @property
    def pk(self):
        return None

    def get_username(self):
        return self.receipt_profile.display_name or f'guest-{str(self.receipt_profile.public_id)[:8]}'

    def __str__(self):
        return self.get_username()


def profile_for(principal):
    return getattr(principal, 'receipt_profile', None)


def family_for(principal):
    profile = profile_for(principal)
    return profile.family if profile and profile.family_id else None


def owner_values(principal):
    profile = profile_for(principal)
    if profile is None:
        raise ValueError('Brak profilu Receipt Saver dla uwierzytelnionego klienta.')
    return {
        'profile': profile,
        'user': profile.user,
        'family': profile.family,
    }


def _visible(queryset, principal):
    if getattr(principal, 'is_superuser', False):
        return queryset
    profile = profile_for(principal)
    if profile is None:
        return queryset.none()
    if profile.family_id:
        return queryset.filter(family=profile.family)
    if profile.user_id:
        return queryset.filter(Q(profile=profile) | Q(profile__isnull=True, user=profile.user))
    return queryset.filter(profile=profile)


def visible_receipts(principal):
    return _visible(Receipt.objects.all(), principal)


def visible_bank_transactions(principal):
    return _visible(BankTransaction.objects.all(), principal)


def visible_bank_import_jobs(principal):
    return _visible(BankImportJob.objects.all(), principal)


def visible_undo_operations(principal):
    return _visible(UndoOperation.objects.all(), principal)
