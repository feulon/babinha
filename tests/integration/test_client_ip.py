import pytest
from django.test import RequestFactory

from accounts.throttle import client_key
from core.request import client_ip


@pytest.fixture
def factory() -> RequestFactory:
    return RequestFactory()


def test_uses_remote_addr_by_default(factory: RequestFactory, settings) -> None:
    settings.BABA_CLIENT_IP_HEADER = ""
    request = factory.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4")
    assert client_ip(request) == "10.0.0.5"


def test_trusted_proxy_header_takes_the_hop_the_proxy_appended(
    factory: RequestFactory, settings
) -> None:
    settings.BABA_CLIENT_IP_HEADER = "HTTP_X_FORWARDED_FOR"
    request = factory.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="9.9.9.9, 1.2.3.4")
    assert client_ip(request) == "1.2.3.4"


def test_spoofed_forwarded_entries_do_not_change_the_client_ip(
    factory: RequestFactory, settings
) -> None:
    settings.BABA_CLIENT_IP_HEADER = "HTTP_X_FORWARDED_FOR"
    seen = {
        client_ip(factory.get("/", HTTP_X_FORWARDED_FOR=f"{spoofed}, 1.2.3.4"))
        for spoofed in ("5.5.5.5", "6.6.6.6", "7.7.7.7")
    }
    assert seen == {"1.2.3.4"}


def test_single_value_header(factory: RequestFactory, settings) -> None:
    settings.BABA_CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"
    request = factory.get("/", REMOTE_ADDR="10.0.0.5", HTTP_CF_CONNECTING_IP="1.2.3.4")
    assert client_ip(request) == "1.2.3.4"


def test_falls_back_when_the_header_is_missing(factory: RequestFactory, settings) -> None:
    settings.BABA_CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"
    request = factory.get("/", REMOTE_ADDR="10.0.0.5")
    assert client_ip(request) == "10.0.0.5"


@pytest.mark.parametrize(
    ("ip", "key"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("2001:db8:0:1::ff", "2001:db8:0:1::/64"),
        ("2001:DB8:0:1:aaaa:bbbb:cccc:dddd", "2001:db8:0:1::/64"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("unknown", "unknown"),
    ],
)
def test_limits_count_an_ipv4_address_or_an_ipv6_64(ip: str, key: str) -> None:
    assert client_key(ip) == key
