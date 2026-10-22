"""Database-backed rate limiting, shared by every app process.

Login lockout is keyed on account + client, so a stranger who types someone else's phone or
e-mail wrong five times only locks themselves out, not the account owner. A coarse per-client limit
then bounds how many guesses one client can make across all accounts. The client IP is used rather
than a device cookie because a cookie is under the attacker's control and could be rotated.

Changing IP is cheap too, so a wider per-account limit counts every failure whatever the client:
it caps how fast anyone can guess one password. Hitting it locks the owner out as well, until it
lifts or the diretoria hands out a new password (see release_login). IPv6 clients are grouped by
/64, the block any single connection or server gets, so cycling addresses inside it does not help.
"""

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta

from accounts.models import ThrottleEvent
from core import clock

RETENTION = timedelta(days=1)


@dataclass(frozen=True)
class Limit:
    scope: str
    max_events: int
    window: timedelta

    def blocked_until(self, key: str) -> datetime | None:
        """When the block on `key` lifts, or None if it is not blocked right now."""
        now = clock.now()
        recent = ThrottleEvent.objects.filter(
            scope=self.scope, key=key, at__gt=now - self.window
        ).order_by("-at")
        times = list(recent.values_list("at", flat=True)[: self.max_events])
        if len(times) < self.max_events:
            return None
        return times[0] + self.window

    def hit(self, key: str) -> None:
        now = clock.now()
        ThrottleEvent.objects.create(scope=self.scope, key=key, at=now)
        ThrottleEvent.objects.filter(scope=self.scope, at__lt=now - RETENTION).delete()

    def clear(self, key: str) -> None:
        ThrottleEvent.objects.filter(scope=self.scope, key=key).delete()


LOGIN_ACCOUNT_CLIENT = Limit("login:account-client", max_events=5, window=timedelta(minutes=15))
LOGIN_ACCOUNT = Limit("login:account", max_events=20, window=timedelta(hours=1))
LOGIN_CLIENT = Limit("login:client", max_events=30, window=timedelta(minutes=15))
PASSWORD_RESET_CLIENT = Limit("reset:client", max_events=10, window=timedelta(hours=1))
PASSWORD_RESET_ACCOUNT = Limit("reset:account", max_events=3, window=timedelta(hours=1))
PASSWORD_CHANGE_ACCOUNT = Limit(
    "password-change:account", max_events=5, window=timedelta(minutes=15)
)
FEEDBACK_ACCOUNT = Limit("feedback:account", max_events=5, window=timedelta(hours=1))
SIGNUP_CLIENT = Limit("signup:client", max_events=10, window=timedelta(hours=1))


IPV6_GROUP_PREFIX = 64


def client_key(ip: str) -> str:
    """What the per-client limits count against: the IPv4 address, or the IPv6 /64 it is in."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 4:
        return str(address)
    if address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(ipaddress.ip_network(f"{address}/{IPV6_GROUP_PREFIX}", strict=False))


def account_client_key(account: str, client: str) -> str:
    return f"{account.strip().lower()}|{client}"


@dataclass(frozen=True)
class LoginBlock:
    until: datetime
    whole_account: bool  # True: no other device gets in either, not only this one


def login_block(account: str, client: str) -> LoginBlock | None:
    """Whether a login for `account` from `client` is refused right now, and until when."""
    account_until = LOGIN_ACCOUNT.blocked_until(account)
    blocks = [
        account_until,
        LOGIN_ACCOUNT_CLIENT.blocked_until(account_client_key(account, client)),
        LOGIN_CLIENT.blocked_until(client),
    ]
    active = [until for until in blocks if until is not None]
    if not active:
        return None
    return LoginBlock(until=max(active), whole_account=account_until is not None)


def record_login_failure(account: str, client: str) -> None:
    LOGIN_ACCOUNT.hit(account)
    LOGIN_ACCOUNT_CLIENT.hit(account_client_key(account, client))
    LOGIN_CLIENT.hit(client)


def record_login_success(account: str, client: str) -> None:
    LOGIN_ACCOUNT_CLIENT.clear(account_client_key(account, client))


def release_login(player_id: int) -> None:
    """Lift every login lockout on the player, from any device: their password just changed."""
    account = f"player:{player_id}"
    LOGIN_ACCOUNT.clear(account)
    ThrottleEvent.objects.filter(
        scope=LOGIN_ACCOUNT_CLIENT.scope, key__startswith=account_client_key(account, "")
    ).delete()
