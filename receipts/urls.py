from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import ReceiptViewSet, import_bank_statement, match_candidates, scan_receipt, summaries

router = DefaultRouter()
router.register('receipts', ReceiptViewSet, basename='receipts')

urlpatterns = [
    path('', include(router.urls)),
    path('receipts/scan/', scan_receipt),
    path('bank/statement/', import_bank_statement),
    path('summaries/', summaries),
    path('matches/review/', match_candidates),
]
