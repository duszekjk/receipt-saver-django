from rest_framework import serializers
from .models import BankTransaction, MatchCandidate, Receipt, ReceiptItem


class ReceiptItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceiptItem
        fields = '__all__'


class ReceiptSerializer(serializers.ModelSerializer):
    items = ReceiptItemSerializer(many=True, read_only=True)
    discount_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Receipt
        fields = '__all__'


class BankTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankTransaction
        fields = '__all__'


class MatchCandidateSerializer(serializers.ModelSerializer):
    receipt = ReceiptSerializer(read_only=True)
    bank_transaction = BankTransactionSerializer(read_only=True)

    class Meta:
        model = MatchCandidate
        fields = '__all__'
