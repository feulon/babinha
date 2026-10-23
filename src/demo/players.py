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
    nickname: str
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
    DemoPlayer(
        "presidente", "Carlos Eduardo Lima", "Careca", M, LINHA, 2, None, is_presidente=True
    ),
    DemoPlayer(
        "diretoria", "Antônio Marcos Souza", "Tiozão", M, LINHA, 3, "presidente", is_diretoria=True
    ),
    DemoPlayer("paredao", "Paulo Roberto Alves", "Paredão", M, GOLEIRO, 2, "presidente"),
    DemoPlayer("maodealface", "Rogério Santana", "Mão de Alface", A, GOLEIRO, 4, "diretoria"),
    DemoPlayer("gatinho", "Gabriel Ferreira", "Gatinho", G, GOLEIRO, 3, "paredao"),
    DemoPlayer("baixinho", "Lucas Pereira", "Baixinho", M, LINHA, 1, "presidente"),
    DemoPlayer("magrao", "Marcelo Ribeiro", "Magrão", M, LINHA, 2, "presidente"),
    DemoPlayer("alemao", "Felipe Schneider", "Alemão", M, LINHA, 1, "diretoria"),
    DemoPlayer("bigode", "José Carlos Barbosa", "Bigode", M, LINHA, 3, "diretoria"),
    DemoPlayer("cabecao", "Ricardo Nascimento", "Cabeção", M, LINHA, 4, "baixinho"),
    DemoPlayer("dentinho", "Thiago Oliveira", "Dentinho", M, LINHA, 2, "magrao"),
    DemoPlayer("pezao", "André Luiz Martins", "Pezão", M, LINHA, 5, "alemao"),
    DemoPlayer("juninho", "Pedro Henrique Costa", "Juninho", M, LINHA, 1, "bigode"),
    DemoPlayer("toquinho", "Bruno Teixeira", "Toquinho", A, LINHA, 3, "baixinho"),
    DemoPlayer("bochecha", "Diego Rocha", "Bochecha", A, LINHA, 4, "magrao"),
    DemoPlayer("canhota", "Rafael Gomes", "Canhota", A, LINHA, 2, "alemao"),
    DemoPlayer("formiga", "Vinícius Araújo", "Formiga", A, LINHA, 3, "cabecao"),
    DemoPlayer("ratinho", "Leandro Cardoso", "Ratinho", A, LINHA, 5, "dentinho"),
    DemoPlayer("fumaca", "Eduardo Moreira", "Fumaça", A, LINHA, 3, "pezao"),
    DemoPlayer("mineiro", "Sérgio Duarte", "Mineiro", A, LINHA, 4, "toquinho"),
    DemoPlayer("pastel", "Fábio Mendes", "Pastel", G, LINHA, 5, "toquinho"),
    DemoPlayer("batata", "Renato Freitas", "Batata", G, LINHA, 4, "bochecha"),
    DemoPlayer("xerife", "Cláudio Nogueira", "Xerife", G, LINHA, 3, "canhota"),
    DemoPlayer("sabia", "Matheus Carvalho", "Sabiá", G, LINHA, 2, "formiga"),
    DemoPlayer("chiclete", "Gustavo Ramos", "Chiclete", G, LINHA, 5, "ratinho"),
    DemoPlayer("tatu", "Henrique Pinto", "Tatu", A, LINHA, 4, "fumaca", rookie=True),
)
