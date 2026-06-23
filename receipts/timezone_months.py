from datetime import datetime, time
from django.utils import timezone


def aware_day_start(value):
    naive = datetime.combine(value, time.min)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive
