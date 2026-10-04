from django.conf import settings


def frontend_base_url(request):
    configured = settings.FRONTEND_BASE_URL.strip()
    if configured:
        return configured.rstrip("/")
    host = request.get_host()
    if settings.DEBUG and host.endswith(":8000"):
        host = host[:-5] + ":3000"
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{host}"
