from django.urls import include, path
from rest_framework.routers import DefaultRouter
from . import bank_import_views
from .views import ReceiptViewSet, dashboard, dashboard_subcategory_details, match_candidates, me, scan_receipt, summaries

router = DefaultRouter()
router.register('receipts', ReceiptViewSet, basename='receipts')

urlpatterns = [
    path('me/', me),
    path('dashboard/', dashboard),
    path('dashboard/subcategory/', dashboard_subcategory_details),
    path('receipts/scan/', scan_receipt),
    path('bank/statement/', bank_import_views.import_bank_statement),
    path('bank/import/', bank_import_views.import_bank_statement),
    path('bank/import/status/latest/', bank_import_views.latest_bank_import_status),
    path('bank/import/status/<str:job_id>/', bank_import_views.bank_import_status),
    path('summaries/', summaries),
    path('matches/review/', match_candidates),
    path('', include(router.urls)),
]
