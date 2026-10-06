from django.http import JsonResponse


def healthz(request):
    """Liveness probe; deployment startup checks the database via migrations."""
    return JsonResponse({"status": "ok"})
