from django.core.exceptions import ValidationError

PASSWORD_LENGTH = 4


class FourCharacterPasswordValidator:
    """The baba uses short passwords by product decision: exactly 4 characters."""

    def validate(self, password: str, user: object = None) -> None:
        if len(password) != PASSWORD_LENGTH:
            raise ValidationError(
                f"A senha deve ter exatamente {PASSWORD_LENGTH} caracteres.",
                code="password_length",
            )

    def get_help_text(self) -> str:
        return f"A senha deve ter exatamente {PASSWORD_LENGTH} caracteres."


BLOCKED_PASSWORD = "1234"
BLOCKED_PASSWORD_MESSAGE = "1234, tá de sacanagem?"


def reject_blocked_password(password: str) -> None:
    """The joke the forms and criar_presidente play on anyone choosing exactly 1234. It lives
    outside AUTH_PASSWORD_VALIDATORS on purpose: the demo seed creates 1234 accounts that must
    keep working, and existing 1234 accounts still sign in."""
    if password == BLOCKED_PASSWORD:
        raise ValidationError(BLOCKED_PASSWORD_MESSAGE, code="password_blocked")
