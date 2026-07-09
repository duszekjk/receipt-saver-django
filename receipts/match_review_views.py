from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .models import MatchCandidate
from .serializers import MatchCandidateSerializer
from .views import API_AUTHENTICATION, user_family


def _visible_match_candidates(user):
    qs = MatchCandidate.objects.select_related('receipt', 'bank_transaction').prefetch_related('receipt__items')
    if user.is_superuser:
        return qs
    family = user_family(user)
    if family:
        return qs.filter(receipt__family=family)
    return qs.filter(receipt__user=user)


def _get_candidate(user, candidate_id):
    return _visible_match_candidates(user).filter(id=candidate_id).first()


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def accept_match_candidate(request, candidate_id):
    candidate = _get_candidate(request.user, candidate_id)
    if not candidate:
        return Response({'detail': 'Dopasowanie nie istnieje.'}, status=404)
    tx = candidate.bank_transaction
    tx.matched_receipt = candidate.receipt
    tx.save(update_fields=['matched_receipt'])
    candidate.status = 'auto_matched'
    candidate.save(update_fields=['status'])
    MatchCandidate.objects.filter(bank_transaction=tx, status='needs_review').exclude(id=candidate.id).update(status='rejected')
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
