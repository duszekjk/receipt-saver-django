from django.db import transaction
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
    id = serializers.IntegerField(required=False)

    class Meta:
        model = ReceiptItem
        fields = '__all__'
        read_only_fields = ['receipt']


class ReceiptSerializer(serializers.ModelSerializer):
    items = ReceiptItemSerializer(many=True, required=False)
    discount_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Receipt
        fields = '__all__'
        read_only_fields = ['user', 'family', 'image', 'content_fingerprint', 'duplicate_of', 'raw_openai_json', 'created_at']

    def validate_currency(self, value):
        value = (value or '').strip().upper()
        if not value or len(value) > 8:
            raise serializers.ValidationError('Niepoprawna waluta.')
        return value

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if items_data is not None:
            existing = {item.id: item for item in instance.items.all()}
            retained_ids = set()
            for item_data in items_data:
                item_id = item_data.pop('id', None)
                if item_id is not None:
                    item = existing.get(item_id)
                    if item is None:
                        raise serializers.ValidationError({'items': f'Pozycja {item_id} nie należy do tego paragonu.'})
                    for field, value in item_data.items():
                        setattr(item, field, value)
                    item.save()
                    retained_ids.add(item.id)
                else:
                    item = ReceiptItem.objects.create(receipt=instance, **item_data)
                    retained_ids.add(item.id)
            instance.items.exclude(id__in=retained_ids).delete()

        return instance


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
