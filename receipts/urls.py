from django.urls import include, path
from rest_framework.routers import DefaultRouter
from . import bank_import_views, bank_transaction_item_views, dashboard_views, email_import_views, guest_views, lifecycle_views, match_review_views, receipt_edit_views, undo_views
from .views import ReceiptViewSet, match_candidates, me, scan_receipt, set_receipt_date, summaries

router = DefaultRouter()
router.register('receipts', ReceiptViewSet, basename='receipts')

urlpatterns = [
    path('guest/register/', guest_views.register_guest),
    path('me/', me),
    path('dashboard/', dashboard_views.dashboard),
    path('dashboard/subcategory/', dashboard_views.dashboard_subcategory_details),
    path('undo/', undo_views.undo),
    path('undo/status/', undo_views.undo_status),
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
    path('lifecycle/rules/', lifecycle_views.cycle_rules),
    path('lifecycle/rules/<int:rule_id>/', lifecycle_views.delete_cycle_rule),
    path('lifecycle/suggestion/', lifecycle_views.cycle_suggestion),
    path('search/', lifecycle_views.purchase_search),
    path('summaries/', summaries),
    path('matches/review/', match_candidates),
    path('matches/review/<int:candidate_id>/accept/', match_review_views.accept_match_candidate),
    path('matches/review/<int:candidate_id>/reject/', match_review_views.reject_match_candidate),
    path('', include(router.urls)),
]
