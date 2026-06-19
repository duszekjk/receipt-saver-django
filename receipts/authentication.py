import hashlib
import hmac
from datetime import timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import authentication, exceptions
from .models import AppLoginNonce, AppLoginToken


class AppTokenAuthentication(authentication.BaseAuthentication):
    max_clock_skew = timedelta(minutes=5)

    def authenticate(self, request):
        device_id = request.headers.get('X-Receipt-Device')
        timestamp = request.headers.get('X-Receipt-Timestamp')
        nonce = request.headers.get('X-Receipt-Nonce')
        signed_path = request.headers.get('X-Receipt-Path') or request.get_full_path()
        signature = request.headers.get('X-Receipt-Signature')

        if not all([device_id, timestamp, nonce, signed_path, signature]):
            return None

        try:
            token = AppLoginToken.objects.select_related('profile__user').get(device_id=device_id, is_active=True)
        except AppLoginToken.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid device token')

        ts = parse_datetime(timestamp)
        if ts is None:
            raise exceptions.AuthenticationFailed('Invalid timestamp')
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts, timezone.utc)
        if abs(timezone.now() - ts) > self.max_clock_skew:
            raise exceptions.AuthenticationFailed('Expired timestamp')

        if AppLoginNonce.objects.filter(token=token, nonce=nonce).exists():
            raise exceptions.AuthenticationFailed('Nonce already used')

        body_hash = hashlib.sha256(request.body or b'').hexdigest()
        payload = '\n'.join([
            request.method.upper(),
            signed_path,
            timestamp,
            nonce,
            body_hash,
        ])
        expected = hmac.new(token.secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise exceptions.AuthenticationFailed('Invalid signature')

        AppLoginNonce.objects.create(token=token, nonce=nonce, timestamp=ts)
        token.mark_used()
        return token.profile.user, token
