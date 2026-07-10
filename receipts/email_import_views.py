from rest_framework import permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .email_import import EmailImportError, analyze_purchase_email, apply_email_analysis, extract_attachment_text
from .views import API_AUTHENTICATION


@api_view(['POST'])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([permissions.IsAuthenticated])
def import_purchase_email(request):
    text = request.data.get('text') or ''
    files = request.FILES.getlist('files')
    attachment_parts = []
    for upload in files:
        extracted = extract_attachment_text(upload)
        if extracted:
            attachment_parts.append(f'Załącznik {upload.name}:\n{extracted}')

    try:
        attachment_text = '\n\n'.join(attachment_parts)
        analysis = analyze_purchase_email(text, attachment_text)
        tx = apply_email_analysis(request.user, analysis, source_text='\n\n'.join([text, attachment_text]))
    except EmailImportError as error:
        return Response({'detail': str(error)}, status=422)
    except ValueError as error:
        return Response({'detail': f'Nie udało się poprawnie sklasyfikować zakupu: {error}'}, status=422)
    except Exception as error:
        return Response({'detail': f'Nie udało się zaimportować wiadomości: {error}'}, status=500)

    return Response({
        'matched': tx is not None,
        'transaction_id': tx.id if tx else None,
        'merchant_name': analysis.get('merchant_name') or '',
        'purchase_description': analysis.get('purchase_description') or '',
        'purchased_at': analysis.get('purchased_at'),
        'amount': str(analysis.get('amount') or ''),
        'currency': analysis.get('currency') or '',
        'category': analysis.get('category') or '',
        'subcategory': analysis.get('subcategory') or '',
        'message': 'Dopasowano i uzupełniono transakcję.' if tx else 'Odczytano zakup, ale nie znaleziono pasującej transakcji bankowej.',
    })
