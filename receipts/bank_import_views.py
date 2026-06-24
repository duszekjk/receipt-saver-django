import threading
from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from .bank_import_jobs import process_bank_import_job, serialize_bank_import_job
from .models import BankImportJob
from .views import API_AUTHENTICATION, user_family


def _start_background_import(job_id):
    thread = threading.Thread(target=process_bank_import_job, args=(job_id,), daemon=True)
    thread.start()


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def import_bank_statement(request):
    file = request.FILES.get('file')
    bank = request.data.get('bank', 'unknown')
    if not file:
        return Response({'detail': 'Missing file'}, status=400)

    family = user_family(request.user)
    job = BankImportJob.objects.create(
        user=request.user,
        family=family,
        bank=bank,
        source_file=file,
        source_file_name=file.name,
    )
    _start_background_import(str(job.id))
    return Response(serialize_bank_import_job(job), status=202)


def _visible_jobs_for_user(user):
    qs = BankImportJob.objects.all()
    if not user.is_superuser:
        qs = qs.filter(user=user)
    return qs


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def latest_bank_import_status(request):
    job = _visible_jobs_for_user(request.user).order_by('-created_at').first()
    if not job:
        return Response({'detail': 'Import job not found'}, status=404)
    return Response(serialize_bank_import_job(job))


@api_view(['GET'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def bank_import_status(request, job_id):
    job = _visible_jobs_for_user(request.user).filter(id=job_id).first()
    if not job:
        return Response({'detail': 'Import job not found'}, status=404)
    return Response(serialize_bank_import_job(job))
