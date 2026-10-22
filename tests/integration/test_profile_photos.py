"""Profile photos: optional at sign-up, changeable later, set by the diretoria, shown as a round
avatar with the initial as fallback, and private to signed-in players."""

import io
from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from accounts import photos
from accounts.models import Player

pytestmark = pytest.mark.django_db

SIGNUP = reverse("accounts:signup")
PHOTO_EDIT = reverse("accounts:photo_edit")
PHOTO_REMOVE = reverse("accounts:photo_remove")
PLAYERS = reverse("community:players")


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (600, 400), **save: object) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, fmt, **save)
    return out.getvalue()


def upload(content: bytes, name: str = "eu.jpg") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def photo_url(player: Player) -> str:
    return reverse("accounts:photo", args=[player.pk])


def stored(player: Player) -> Image.Image:
    player.refresh_from_db()
    with photos.photo_storage().open(player.photo, "rb") as handle:
        image = Image.open(io.BytesIO(handle.read()))
        image.load()
    return image


@pytest.fixture
def signed_in(make_player: Callable[..., Player], president: Player) -> tuple[Client, Player]:
    player = make_player("Zico", referred_by=president)
    client = Client()
    client.force_login(player)
    return client, player


def signup_data(referrer: Player, **extra: object) -> dict:
    return {
        "name": "Zico",
        "phone": "(21) 98888-7777",
        "email": "zico@example.com",
        "referrer_name": referrer.name,
        "password": "gol1",
        "password_confirm": "gol1",
        "accept_rules": "on",
        **extra,
    }


class TestProcessing:
    def test_a_photo_becomes_a_small_square_jpeg(self) -> None:
        data = photos.process(upload(image_bytes("PNG", (900, 300))))
        image = Image.open(io.BytesIO(data))
        assert (image.format, image.size) == ("JPEG", (photos.SIDE, photos.SIDE))

    def test_exif_metadata_is_dropped(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "Camera Maker"
        exif[0x8825] = {1: "S", 2: (23.0, 32.0, 0.0)}  # GPS block
        data = photos.process(upload(image_bytes("JPEG", exif=exif)))
        assert not Image.open(io.BytesIO(data)).getexif()
        assert b"Camera Maker" not in data

    def test_the_orientation_is_applied_before_the_metadata_goes(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 6  # rotated 90 degrees: the stored pixels must already be upright
        left_red = Image.new("RGB", (40, 20), (0, 0, 255))
        left_red.paste((255, 0, 0), (0, 0, 20, 20))
        out = io.BytesIO()
        left_red.save(out, "JPEG", exif=exif)
        result = Image.open(io.BytesIO(photos.process(upload(out.getvalue()))))
        top, bottom = result.getpixel((128, 20)), result.getpixel((128, 236))
        assert top[0] > 150 > top[2]
        assert bottom[2] > 150 > bottom[0]

    @pytest.mark.parametrize(
        "content",
        [
            b"",
            b"not an image at all",
            b"%PDF-1.7\n",
            b"GIF89a" + b"\x00" * 40,
            b"\xff\xd8\xff" + b"x",
        ],
    )
    def test_anything_that_is_not_a_supported_image_is_refused(self, content: bytes) -> None:
        with pytest.raises(photos.PhotoError):
            photos.process(upload(content))

    def test_the_claimed_type_and_name_do_not_matter(self) -> None:
        with pytest.raises(photos.PhotoError):
            photos.process(upload(b"<script>alert(1)</script>", "foto.jpg"))
        assert photos.process(upload(image_bytes("WEBP"), "foto.txt"))

    def test_a_gif_is_refused_even_though_it_is_an_image(self) -> None:
        with pytest.raises(photos.PhotoError):
            photos.process(upload(image_bytes("GIF")))

    def test_no_other_decoder_ever_reads_the_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Image.init()
        factory, accept = Image.OPEN["XBM"]
        opened = []

        def spy(fp: object, filename: object) -> Image.Image:
            opened.append("XBM")
            return factory(fp, filename)

        monkeypatch.setitem(Image.OPEN, "XBM", (spy, accept))
        xbm = b"#define x_width 1\n#define x_height 1\nstatic char x_bits[] = { 0x00 };\n"
        with pytest.raises(photos.PhotoError):
            photos.process(upload(xbm))
        assert opened == []

    def test_a_huge_file_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(photos, "MAX_BYTES", 100)
        with pytest.raises(photos.PhotoError, match="grande demais"):
            photos.process(upload(image_bytes("PNG")))

    def test_an_image_with_too_many_pixels_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(photos, "MAX_PIXELS", 100)
        with pytest.raises(photos.PhotoError, match="grande demais"):
            photos.process(upload(image_bytes("PNG", (50, 50))))


class TestSignup:
    def test_the_photo_is_optional(self, president: Player) -> None:
        response = Client().post(SIGNUP, signup_data(president))
        assert response.status_code == 302
        assert Player.objects.get(name="Zico").photo == ""

    def test_a_photo_sent_at_signup_is_stored_small(self, president: Player) -> None:
        data = signup_data(president, photo=upload(image_bytes("PNG", (1200, 900))))
        assert Client().post(SIGNUP, data).status_code == 302
        player = Player.objects.get(name="Zico")
        assert player.photo.startswith("fotos/")
        assert player.photo.endswith(".jpg")
        assert stored(player).size == (photos.SIDE, photos.SIDE)

    def test_a_bad_file_stops_the_signup_with_a_message(self, president: Player) -> None:
        data = signup_data(president, photo=upload(b"just text", "x.jpg"))
        response = Client().post(SIGNUP, data)
        assert response.status_code == 200
        assert "Envie uma foto em JPG, PNG ou WebP." in response.content.decode()
        assert not Player.objects.filter(name="Zico").exists()


class TestOwnPhoto:
    def test_sending_and_swapping_the_photo(self, signed_in: tuple[Client, Player]) -> None:
        client, player = signed_in
        assert client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))}).status_code == 302
        player.refresh_from_db()
        first = player.photo
        assert first
        client.post(PHOTO_EDIT, {"photo": upload(image_bytes("WEBP"))})
        player.refresh_from_db()
        assert player.photo != first
        assert not photos.photo_storage().exists(first)  # the old file is gone

    def test_an_invalid_file_keeps_the_current_photo(
        self, signed_in: tuple[Client, Player]
    ) -> None:
        client, player = signed_in
        client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))})
        player.refresh_from_db()
        before = player.photo
        response = client.post(PHOTO_EDIT, {"photo": upload(b"nope")})
        assert response.status_code == 200
        assert "Envie uma foto" in response.content.decode()
        player.refresh_from_db()
        assert player.photo == before

    def test_sending_nothing_asks_for_a_file(self, signed_in: tuple[Client, Player]) -> None:
        client, _ = signed_in
        assert client.post(PHOTO_EDIT, {}).status_code == 200

    def test_removing_the_photo_deletes_the_file(self, signed_in: tuple[Client, Player]) -> None:
        client, player = signed_in
        client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))})
        player.refresh_from_db()
        name = player.photo
        assert client.post(PHOTO_REMOVE).status_code == 302
        player.refresh_from_db()
        assert player.photo == ""
        assert not photos.photo_storage().exists(name)

    def test_removing_needs_a_post(self, signed_in: tuple[Client, Player]) -> None:
        assert signed_in[0].get(PHOTO_REMOVE).status_code == 405


class TestServing:
    def test_signed_in_players_see_any_photo(
        self, signed_in: tuple[Client, Player], make_player: Callable[..., Player]
    ) -> None:
        _, owner = signed_in
        owner_client = signed_in[0]
        owner_client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))})
        viewer = Client()
        viewer.force_login(make_player("Outro"))
        response = viewer.get(photo_url(owner))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert "private" in response["Cache-Control"]
        assert Image.open(io.BytesIO(b"".join(response.streaming_content))).size == (256, 256)

    def test_a_visitor_is_sent_to_the_login(self, signed_in: tuple[Client, Player]) -> None:
        client, player = signed_in
        client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))})
        response = Client().get(photo_url(player))
        assert response.status_code == 302
        assert "/entrar/" in response["Location"]

    def test_the_upload_and_remove_screens_need_a_login(self) -> None:
        assert Client().get(PHOTO_EDIT).status_code == 302
        assert Client().post(PHOTO_REMOVE).status_code == 302

    def test_nobody_without_a_photo_has_a_file_to_fetch(
        self, signed_in: tuple[Client, Player]
    ) -> None:
        client, player = signed_in
        assert client.get(photo_url(player)).status_code == 404
        assert client.get(reverse("accounts:photo", args=[99999])).status_code == 404


class TestBoard:
    @pytest.fixture
    def board_client(self, board_member: Player) -> Client:
        client = Client()
        client.force_login(board_member)
        return client

    def test_creating_a_player_with_a_photo(self, board_client: Client) -> None:
        data = {
            "name": "Novato",
            "phone": "21999990000",
            "email": "novato@example.com",
            "player_type": "avulso",
            "position": "linha",
            "pote": 3,
            "photo": upload(image_bytes("JPEG")),
        }
        assert board_client.post(reverse("board:player_new"), data).status_code == 302
        assert Player.objects.get(name="Novato").photo.startswith("fotos/")

    def test_creating_a_player_without_a_photo_still_works(self, board_client: Client) -> None:
        data = {
            "name": "Novato",
            "phone": "21999990000",
            "email": "novato@example.com",
            "player_type": "avulso",
            "position": "linha",
            "pote": 3,
        }
        assert board_client.post(reverse("board:player_new"), data).status_code == 302
        assert Player.objects.get(name="Novato").photo == ""

    def edit(self, client: Client, player: Player, **extra: object):
        data = {
            "name": player.name,
            "phone": player.phone,
            "email": player.email or "",
            "player_type": player.player_type,
            "position": player.position,
            "pote": player.pote,
            "is_active": "on",
            "referred_by": player.referred_by_id,
            **extra,
        }
        return client.post(reverse("board:player_edit", args=[player.pk]), data)

    def test_the_board_sets_replaces_and_removes_a_photo(
        self, board_client: Client, make_player: Callable[..., Player], president: Player
    ) -> None:
        player = make_player("Rafa", referred_by=president)
        assert self.edit(board_client, player, photo=upload(image_bytes("PNG"))).status_code == 302
        player.refresh_from_db()
        assert player.photo
        edit_page = board_client.get(reverse("board:player_edit", args=[player.pk]))
        assert "Remover a foto atual" in edit_page.content.decode()
        assert self.edit(board_client, player, remove_photo="on").status_code == 302
        player.refresh_from_db()
        assert player.photo == ""

    def test_saving_without_a_file_keeps_the_photo(
        self, board_client: Client, make_player: Callable[..., Player], president: Player
    ) -> None:
        player = make_player("Rafa", referred_by=president)
        self.edit(board_client, player, photo=upload(image_bytes("PNG")))
        player.refresh_from_db()
        name = player.photo
        self.edit(board_client, player, name="Rafa Silva")
        player.refresh_from_db()
        assert player.photo == name

    def test_a_bad_file_is_refused_on_the_edit_screen(
        self, board_client: Client, make_player: Callable[..., Player], president: Player
    ) -> None:
        player = make_player("Rafa", referred_by=president)
        response = self.edit(board_client, player, photo=upload(b"junk"))
        assert response.status_code == 200
        assert "Envie uma foto" in response.content.decode()


class TestViewPhoto:
    def test_any_player_opens_the_enlarged_photo(
        self, signed_in: tuple[Client, Player], make_player: Callable[..., Player]
    ) -> None:
        client, owner = signed_in
        client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))})
        viewer = Client()
        viewer.force_login(make_player("Outro"))
        response = viewer.get(reverse("accounts:photo_page", args=[owner.pk]))
        assert response.status_code == 200
        html = response.content.decode()
        assert photo_url(owner) in html
        assert reverse("community:player", args=[owner.pk]) in html

    def test_no_photo_means_no_page(self, signed_in: tuple[Client, Player]) -> None:
        client, player = signed_in
        assert client.get(reverse("accounts:photo_page", args=[player.pk])).status_code == 404

    def test_a_visitor_is_sent_to_the_login(self, signed_in: tuple[Client, Player]) -> None:
        client, player = signed_in
        client.post(PHOTO_EDIT, {"photo": upload(image_bytes("PNG"))})
        response = Client().get(reverse("accounts:photo_page", args=[player.pk]))
        assert response.status_code == 302
        assert "/entrar/" in response["Location"]
