from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """Client IP, honoring the configured trusted proxy header when there is one."""
    header = settings.BABA_CLIENT_IP_HEADER
    if header:
        forwarded = request.META.get(header, "")
        # X-Forwarded-For style headers hold "client, proxy1, proxy2"; only the last entry was
        # appended by the trusted proxy, everything before it is whatever the client sent.
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.META.get("REMOTE_ADDR", "") or "unknown"
