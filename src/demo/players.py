"""The fictitious squad of the demo. Every account lives under DEMO_EMAIL_DOMAIN."""

from dataclasses import dataclass

from accounts.models import PlayerType, Position

DEMO_EMAIL_DOMAIN = "demo.baba"
DEMO_PASSWORD = "1234"
DEMO_PHONE_PREFIX = "9999"

M, A, G = PlayerType.MENSALISTA, PlayerType.AVULSO, PlayerType.AGREGADO
LINHA, GOLEIRO = Position.LINHA, Position.GOLEIRO


@dataclass(frozen=True)
class DemoPlayer:
    key: str
    name: str
    player_type: PlayerType
    position: Position
    pote: int
    referred_by: str | None
    is_presidente: bool = False
    is_diretoria: bool = False
    rookie: bool = False  # never played with the group: the teams rule binds them to the inviter

    @property
    def email(self) -> str:
        return f"{self.key}@{DEMO_EMAIL_DOMAIN}"

    @property
    def phone(self) -> str:
        """A valid, obviously fake mobile (DDD 11, 9 prefix) that is unique per player."""
        return f"119{DEMO_PHONE_PREFIX}{PLAYERS.index(self) + 1:04d}"


def is_demo_email(email: str | None) -> bool:
    """Players without an e-mail are real ones: the demo always gives its accounts one."""
    return bool(email) and email.lower().endswith(f"@{DEMO_EMAIL_DOMAIN}")


# Referrers always come before the players they referred.
PLAYERS = (
    DemoPlayer("presidente", "Careca", M, LINHA, 2, None, is_presidente=True),
    DemoPlayer("diretoria", "Tiozão", M, LINHA, 3, "presidente", is_diretoria=True),
    DemoPlayer("paredao", "Paredão", M, GOLEIRO, 2, "presidente"),
    DemoPlayer("maodealface", "Mão de Alface", A, GOLEIRO, 4, "diretoria"),
    DemoPlayer("gatinho", "Gatinho", G, GOLEIRO, 3, "paredao"),
    DemoPlayer("baixinho", "Baixinho", M, LINHA, 1, "presidente"),
    DemoPlayer("magrao", "Magrão", M, LINHA, 2, "presidente"),
    DemoPlayer("alemao", "Alemão", M, LINHA, 1, "diretoria"),
    DemoPlayer("bigode", "Bigode", M, LINHA, 3, "diretoria"),
    DemoPlayer("cabecao", "Cabeção", M, LINHA, 4, "baixinho"),
    DemoPlayer("dentinho", "Dentinho", M, LINHA, 2, "magrao"),
    DemoPlayer("pezao", "Pezão", M, LINHA, 5, "alemao"),
    DemoPlayer("juninho", "Juninho", M, LINHA, 1, "bigode"),
    DemoPlayer("toquinho", "Toquinho", A, LINHA, 3, "baixinho"),
    DemoPlayer("bochecha", "Bochecha", A, LINHA, 4, "magrao"),
    DemoPlayer("canhota", "Canhota", A, LINHA, 2, "alemao"),
    DemoPlayer("formiga", "Formiga", A, LINHA, 3, "cabecao"),
    DemoPlayer("ratinho", "Ratinho", A, LINHA, 5, "dentinho"),
    DemoPlayer("fumaca", "Fumaça", A, LINHA, 3, "pezao"),
    DemoPlayer("mineiro", "Mineiro", A, LINHA, 4, "toquinho"),
    DemoPlayer("pastel", "Pastel", G, LINHA, 5, "toquinho"),
    DemoPlayer("batata", "Batata", G, LINHA, 4, "bochecha"),
    DemoPlayer("xerife", "Xerife", G, LINHA, 3, "canhota"),
    DemoPlayer("sabia", "Sabiá", G, LINHA, 2, "formiga"),
    DemoPlayer("chiclete", "Chiclete", G, LINHA, 5, "ratinho"),
    DemoPlayer("tatu", "Tatu", A, LINHA, 4, "fumaca", rookie=True),
)
