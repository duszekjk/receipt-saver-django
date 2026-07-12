from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .models import MatchCandidate
from .profile_access import visible_receipts
from .serializers import MatchCandidateSerializer
from .undo_service import record_undo
from .views import API_AUTHENTICATION


def _visible_match_candidates(principal):
    receipt_ids = visible_receipts(principal).values_list('id', flat=True)
    return MatchCandidate.objects.filter(receipt_id__in=receipt_ids).select_related('receipt', 'bank_transaction').prefetch_related('receipt__items')


def _get_candidate(principal, candidate_id):
    return _visible_match_candidates(principal).filter(id=candidate_id).first()


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def accept_match_candidate(request, candidate_id):
    candidate = _get_candidate(request.user, candidate_id)
    if not candidate:
        return Response({'detail': 'Dopasowanie nie istnieje.'}, status=404)
    tx = candidate.bank_transaction
    related = MatchCandidate.objects.filter(bank_transaction=tx)
    previous_statuses = {str(item.id): item.status for item in related}
    previous_matched_receipt_id = tx.matched_receipt_id

    tx.matched_receipt = candidate.receipt
    tx.save(update_fields=['matched_receipt'])
    candidate.status = 'auto_matched'
    candidate.save(update_fields=['status'])
    MatchCandidate.objects.filter(bank_transaction=tx, status='needs_review').exclude(id=candidate.id).update(status='rejected')

    record_undo(
        request.user,
        'match_accept',
        f'Dopasowanie {candidate.receipt.merchant_name or "paragonu"}',
        {
            'action': 'restore_match',
            'transaction_id': tx.id,
            'previous_matched_receipt_id': previous_matched_receipt_id,
            'candidate_statuses': previous_statuses,
        },
    )
    return Response(MatchCandidateSerializer(candidate).data)


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def reject_match_candidate(request, candidate_id):
    candidate = _get_candidate(request.user, candidate_id)
    if not candidate:
        return Response({'detail': 'Dopasowanie nie istnieje.'}, status=404)
    if candidate.bank_transaction.matched_receipt_id == candidate.receipt_id:
        tx = candidate.bank_transaction
        tx.matched_receipt = None
        tx.save(update_fields=['matched_receipt'])
    candidate.status = 'rejected'
    candidate.save(update_fields=['status'])
    return Response(MatchCandidateSerializer(candidate).data)
