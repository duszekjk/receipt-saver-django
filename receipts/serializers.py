from rest_framework import serializers
from .models import BankTransaction, Family, MatchCandidate, Receipt, ReceiptItem, ReceiptUserProfile


class FamilySerializer(serializers.ModelSerializer):
    class Meta:
        model = Family
        fields = '__all__'


class ReceiptUserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = ReceiptUserProfile
        fields = '__all__'


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
    direction = serializers.SerializerMethodField()
    expense_amount = serializers.SerializerMethodField()

    class Meta:
        model = BankTransaction
        fields = '__all__'

    def get_direction(self, obj):
        if obj.amount < 0:
            return 'expense'
        if obj.amount > 0:
            return 'income'
        return 'neutral'

    def get_expense_amount(self, obj):
        return abs(obj.amount) if obj.amount < 0 else 0


class MatchCandidateSerializer(serializers.ModelSerializer):
    receipt = ReceiptSerializer(read_only=True)
    bank_transaction = BankTransactionSerializer(read_only=True)

    class Meta:
        model = MatchCandidate
        fields = '__all__'
