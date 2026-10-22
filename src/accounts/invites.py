"""The invite link a player shares: sign-up with who invited and the group code filled in."""

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

REFERRER_PARAM = "indicacao"
CODE_PARAM = "codigo"


def invite_link(request: HttpRequest, inviter_name: str) -> str:
    """Absolute sign-up URL with the inviter's name and, when the group has one, the invite code."""
    params = {REFERRER_PARAM: inviter_name}
    if settings.BABA_INVITE_CODE:
        params[CODE_PARAM] = settings.BABA_INVITE_CODE
    return request.build_absolute_uri(f"{reverse('accounts:signup')}?{urlencode(params)}")


def signup_initial(request: HttpRequest) -> dict[str, str]:
    """Sign-up fields prefilled from an invite link (the code only if the group uses one)."""
    initial = {}
    referrer = " ".join(request.GET.get(REFERRER_PARAM, "").split())[:60]
    if referrer:
        initial["referrer_name"] = referrer
    code = request.GET.get(CODE_PARAM, "").strip()[:64]
    if code:
        initial["invite_code"] = code
    return initial
