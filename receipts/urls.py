from django.urls import include, path
from rest_framework.routers import DefaultRouter
from . import bank_import_views, bank_transaction_item_views, email_import_views, match_review_views, receipt_edit_views
from .views import ReceiptViewSet, dashboard, dashboard_subcategory_details, match_candidates, me, scan_receipt, set_receipt_date, summaries

router = DefaultRouter()
router.register('receipts', ReceiptViewSet, basename='receipts')

urlpatterns = [
    path('me/', me),
    path('dashboard/', dashboard),
    path('dashboard/subcategory/', dashboard_subcategory_details),
    path('receipts/scan/', scan_receipt),
    path('receipts/<int:receipt_id>/date/', set_receipt_date),
    path('receipts/<int:receipt_id>/preview/', receipt_edit_views.receipt_preview),
    path('receipts/<int:receipt_id>/edit/', receipt_edit_views.update_receipt),
    path('receipts/<int:receipt_id>/delete/', receipt_edit_views.delete_receipt),
    path('imports/email/', email_import_views.import_purchase_email),
    path('bank/statement/', bank_import_views.import_bank_statement),
    path('bank/import/', bank_import_views.import_bank_statement),
    path('bank/import/status/latest/', bank_import_views.latest_bank_import_status),
    path('bank/import/status/<str:job_id>/', bank_import_views.bank_import_status),
    path('bank/transactions/', bank_transaction_item_views.bank_transactions),
    path('bank/transactions/<int:transaction_id>/items/', bank_transaction_item_views.bank_transaction_items),
    path('summaries/', summaries),
    path('matches/review/', match_candidates),
    path('matches/review/<int:candidate_id>/accept/', match_review_views.accept_match_candidate),
    path('matches/review/<int:candidate_id>/reject/', match_review_views.reject_match_candidate),
    path('', include(router.urls)),
]
