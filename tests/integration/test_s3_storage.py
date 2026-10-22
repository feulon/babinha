"""Receipts against a real S3-compatible server (the gateway from compose.yaml).

Runs when BABA_TEST_S3_ENDPOINT_URL is set (see "Testes e lint" in the README); CI sets it.
"""

import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import Player
from baba import settings as production_settings
from core.clock import FixedClock
from core.models import ClubSettings
from finance import services as payments
from finance.models import Charge, ChargeKind
from games import services
from tests.conftest import PDF_BYTES, end_game

ENDPOINT = os.environ.get("BABA_TEST_S3_ENDPOINT_URL", "")

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not ENDPOINT, reason="BABA_TEST_S3_ENDPOINT_URL is not set"),
]


@pytest.fixture
def s3_receipts() -> Iterator[None]:
    options = {
        **production_settings.STORAGES["receipts"]["OPTIONS"],
        "endpoint_url": ENDPOINT,
        "access_key": os.environ["BABA_TEST_S3_ACCESS_KEY_ID"],
        "secret_key": os.environ["BABA_TEST_S3_SECRET_ACCESS_KEY"],
        "bucket_name": os.environ.get("BABA_TEST_S3_BUCKET", "baba-test"),
    }
    backend = {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": options}
    with override_settings(STORAGES={**settings.STORAGES, "receipts": backend}):
        storage = storages["receipts"]
        client = storage.connection.meta.client
        buckets = {b["Name"] for b in client.list_buckets()["Buckets"]}
        if options["bucket_name"] not in buckets:
            client.create_bucket(Bucket=options["bucket_name"])
        yield


def test_round_trip_and_bucket_is_private(s3_receipts: None) -> None:
    storage = storages["receipts"]
    name = storage.save(f"comprovantes/teste/{uuid.uuid4().hex}.pdf", ContentFile(PDF_BYTES))
    try:
        assert storage.open(name).read() == PDF_BYTES
        bucket = storage.bucket_name
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(f"{ENDPOINT}/{bucket}/{name}")  # no signature
        assert denied.value.code == 403
    finally:
        storage.delete(name)
    assert not storage.exists(name)


def test_receipt_flow_through_s3(
    s3_receipts: None,
    clock: FixedClock,
    club_values: ClubSettings,
    make_player,
    board_member: Player,
) -> None:
    player = make_player()
    game_day = services.current_game_day()
    services.join_list(game_day.pk, player)
    end_game(game_day)
    charge = Charge.objects.get(player=player, kind=ChargeKind.GAME)
    receipt = payments.submit_receipt(charge.pk, player, SimpleUploadedFile("pix.pdf", PDF_BYTES))
    try:
        client = Client()
        client.force_login(board_member)
        response = client.get(reverse("finance:receipt_file", args=[receipt.pk]))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == PDF_BYTES
    finally:
        storages["receipts"].delete(receipt.file.name)
