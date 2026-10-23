import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts import invites, photos, referrals, throttle
from accounts.forms import (
    ContactForm,
    LoginForm,
    PasswordChangeForm4,
    PasswordResetSetForm,
    PhotoForm,
    SignupForm,
    ThrottledPasswordResetForm,
)
from accounts.models import Player
from core import clock
from core.clock import SAO_PAULO
from core.request import client_ip

logger = logging.getLogger(__name__)


def _blocked_message(block: throttle.LoginBlock) -> str:
    local = block.until.astimezone(SAO_PAULO)
    if block.whole_account:
        return (
            "Muitas tentativas erradas nesta conta: o login está bloqueado em qualquer aparelho. "
            f"Tente de novo a partir das {local:%Hh%M} ou peça à diretoria uma nova senha, "
            "que libera o acesso na hora."
        )
    return (
        "Muitas tentativas erradas: o acesso a partir deste aparelho está bloqueado. "
        f"Tente de novo a partir das {local:%Hh%M} ou peça uma nova senha à diretoria."
    )


def _safe_next(request: HttpRequest) -> str | None:
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return None


def _already_signed_in(request: HttpRequest) -> HttpResponse:
    messages.info(request, "Você já está logado.")
    return redirect(_safe_next(request) or "games:home")


def _lockout_account(identifier: str) -> str:
    """What the lockout counts attempts against: the player when the identifier names one, so the
    phone and the e-mail of one account share a single counter, else the text typed."""
    try:
        return f"player:{Player.objects.find_by_login(identifier).pk}"
    except Player.DoesNotExist:
        return identifier.lower()


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return _already_signed_in(request)
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["login"]
        account = _lockout_account(identifier)
        client = throttle.client_key(client_ip(request))
        block = throttle.login_block(account, client)
        if block is not None:
            form.add_error(None, _blocked_message(block))
        else:
            player = authenticate(
                request, username=identifier, password=form.cleaned_data["password"]
            )
            if player is None:
                throttle.record_login_failure(account, client)
                block = throttle.login_block(account, client)
                form.add_error(
                    None,
                    _blocked_message(block) if block else "Celular, e-mail ou senha incorretos.",
                )
            else:
                throttle.record_login_success(account, client)
                login(request, player)
                if player.rules_seen_at is None:
                    # First sign-in: the rules (and the week's timeline) once, whatever `next` says.
                    Player.objects.filter(pk=player.pk, rules_seen_at__isnull=True).update(
                        rules_seen_at=clock.now()
                    )
                    return redirect("rules")
                return redirect(_safe_next(request) or "games:home")
    return render(request, "accounts/login.html", {"form": form, "next": _safe_next(request)})


@require_http_methods(["GET", "POST"])
def signup_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return _already_signed_in(request)
    has_players = Player.objects.filter(is_active=True).exists()
    client = throttle.client_key(client_ip(request))
    blocked_until = (
        throttle.SIGNUP_CLIENT.blocked_until(client) if request.method == "POST" else None
    )
    if blocked_until is not None:
        # Not even validated: a guesser must not learn whether the invite code was right.
        context = {"form": SignupForm(), "has_players": has_players, "blocked": True}
        return render(request, "accounts/signup.html", context)
    form = SignupForm(
        request.POST or None, request.FILES or None, initial=invites.signup_initial(request)
    )
    if request.method == "POST":
        if form.is_valid():
            try:
                player = form.save()
            except ValidationError as error:  # someone else took the phone or e-mail meanwhile
                form.add_error(None, error)
            else:
                throttle.SIGNUP_CLIENT.hit(client)
                login(request, player, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, f"Bem-vindo ao Babinha, {player.name}!")
                if not form.photo_saved:
                    messages.warning(
                        request, "Sua foto não foi salva. Envie de novo pelo seu perfil."
                    )
                return redirect(_safe_next(request) or "games:home")
        elif "invite_code" in form.errors:
            throttle.SIGNUP_CLIENT.hit(client)  # wrong codes count, so the code cannot be guessed
    context = {"form": form, "has_players": has_players, "next": _safe_next(request)}
    return render(request, "accounts/signup.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def contact_view(request: HttpRequest) -> HttpResponse:
    """Phone and e-mail of the signed-in player. Players from before the phone existed land
    here (see PhoneRequiredMiddleware) until they give one."""
    needs_phone = not request.user.phone
    form = ContactForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Contato salvo.")
        return redirect(_safe_next(request) or "more")
    context = {"form": form, "needs_phone": needs_phone, "next": _safe_next(request)}
    return render(request, "accounts/contact.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def password_change_view(request: HttpRequest) -> HttpResponse:
    """The signed-in player trades their password (the one the diretoria handed out, usually)."""
    account = f"player:{request.user.pk}"
    blocked_until = throttle.PASSWORD_CHANGE_ACCOUNT.blocked_until(account)
    form = PasswordChangeForm4(request.user, request.POST or None)
    if request.method == "POST" and blocked_until is not None:
        form.add_error(
            None,
            "Muitas tentativas erradas. Tente de novo a partir das "
            f"{blocked_until.astimezone(SAO_PAULO):%Hh%M}.",
        )
    elif request.method == "POST" and form.is_valid():
        player = form.save()
        update_session_auth_hash(request, player)  # keep this device signed in
        throttle.PASSWORD_CHANGE_ACCOUNT.clear(account)
        messages.success(request, "Senha alterada.")
        return redirect("more")
    elif request.method == "POST" and "old_password" in form.errors:
        throttle.PASSWORD_CHANGE_ACCOUNT.hit(account)
    return render(request, "accounts/password_change.html", {"form": form})


@login_required
@require_GET
def photo_file(request: HttpRequest, player_id: int) -> FileResponse:
    """Profile photos are for signed-in players only (the bucket itself stays private)."""
    player = get_object_or_404(Player.objects.only("photo"), pk=player_id)
    if not player.photo:
        raise Http404
    try:
        handle = photos.photo_storage().open(player.photo, "rb")
    except Exception as exc:
        logger.exception("Could not read the photo of player %s from storage", player_id)
        raise Http404 from exc
    response = FileResponse(handle, content_type=photos.CONTENT_TYPE)
    # The URL carries the file's version, so a browser can keep it; shared caches must not.
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def photo_page(request: HttpRequest, player_id: int) -> HttpResponse:
    """A player's photo, enlarged, with a way back to their profile."""
    player = get_object_or_404(Player.objects.only("name", "photo"), pk=player_id)
    if not player.photo:
        raise Http404
    return render(request, "accounts/photo_page.html", {"subject": player})


@login_required
@require_http_methods(["GET", "POST"])
def photo_view(request: HttpRequest) -> HttpResponse:
    """The signed-in player sends or swaps their own photo."""
    form = PhotoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        if form.apply_photo(request.user):
            messages.success(request, "Foto salva.")
            return redirect("more")
        form.add_error(None, "Não foi possível salvar a foto agora. Tente de novo.")
    return render(request, "accounts/photo.html", {"form": form})


@login_required
@require_POST
def photo_remove(request: HttpRequest) -> HttpResponse:
    if request.user.photo:
        try:
            photos.replace(request.user, None)
        except Exception:
            logger.exception("Could not remove the photo of player %s", request.user.pk)
            messages.error(request, "Não foi possível remover a foto agora. Tente de novo.")
            return redirect("accounts:photo_edit")
    messages.success(request, "Foto removida.")
    return redirect("more")


REFERRER_RESULTS_LIMIT = 30


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


@login_required
@require_http_methods(["GET", "POST"])
def referrer_view(request: HttpRequest) -> HttpResponse:
    """The signed-in player connects who referred them to a registered player (by name search),
    swaps that person or removes the referral."""
    me = request.user
    if me.is_presidente:  # the founder has nobody above them
        return redirect("community:player", player_id=me.pk)
    if request.method == "POST":
        if request.POST.get("remove"):
            referrals.disconnect(me)
            messages.success(request, "Indicação removida.")
            return redirect("community:player", player_id=me.pk)
        chosen = (
            referrals.candidates(me).filter(pk=_int_or_none(request.POST.get("player"))).first()
        )
        if chosen is None:
            messages.error(request, "Não achamos esse jogador. Busque o nome de novo.")
            return redirect("accounts:referrer")
        referrals.connect(me, chosen)
        messages.success(request, f"Indicação ligada a {chosen.name}.")
        return redirect("community:player", player_id=me.pk)
    query = request.GET.get("q", "").strip()[:60]
    found = referrals.candidates(me, query)
    context = {
        "query": query,
        "results": list(found[: REFERRER_RESULTS_LIMIT + 1]),
        "limit": REFERRER_RESULTS_LIMIT,
    }
    return render(request, "accounts/referrer.html", context)


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    form_class = ThrottledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")

    @property
    def extra_email_context(self) -> dict:
        return {"valid_minutes": settings.PASSWORD_RESET_TIMEOUT // 60}

    def form_valid(self, form: ThrottledPasswordResetForm) -> HttpResponse:
        client = throttle.client_key(client_ip(self.request))
        if throttle.PASSWORD_RESET_CLIENT.blocked_until(client) is not None:
            form.add_error(None, "Muitos pedidos de recuperação. Tente mais tarde.")
            return self.form_invalid(form)
        throttle.PASSWORD_RESET_CLIENT.hit(client)
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = PasswordResetSetForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form: PasswordResetSetForm) -> HttpResponse:
        response = super().form_valid(form)
        throttle.release_login(form.user.pk)  # the owner proved they read the account's e-mail
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
