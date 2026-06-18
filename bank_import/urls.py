from django.urls import path
from .views import import_bank_statement

urlpatterns = [
    path('bank/import/', import_bank_statement),
]
