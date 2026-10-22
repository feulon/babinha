import secrets

from accounts.phone import format_phone
from accounts.validators import PASSWORD_LENGTH

# No 0/O, 1/l/I: the president reads this out or types it into a chat.
PROVISIONAL_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def generate_provisional_password() -> str:
    return "".join(secrets.choice(PROVISIONAL_ALPHABET) for _ in range(PASSWORD_LENGTH))


def credentials_message(phone: str | None, password: str) -> str:
    """What the diretoria sends a player whose login it just created or reset."""
    return (
        f"Seu login no Babinha: celular {format_phone(phone)}, senha {password}. "
        "Troque a senha em Mais > Alterar senha."
    )
