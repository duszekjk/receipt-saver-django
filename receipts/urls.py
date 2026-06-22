from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import ReceiptViewSet, dashboard, dashboard_subcategory_details, import_bank_statement, match_candidates, me, scan_receipt, summaries

router = DefaultRouter()
router.register('receipts', ReceiptViewSet, basename='receipts')

urlpatterns = [
    path('me/', me),
    path('dashboard/', dashboard),
    path('dashboard/subcategory/', dashboard_subcategory_details),
    path('receipts/scan/', scan_receipt),
    path('bank/statement/', import_bank_statement),
    path('summaries/', summaries),
    path('matches/review/', match_candidates),
    path('', include(router.urls)),
]
