from accounts.passwords import PROVISIONAL_ALPHABET, generate_provisional_password
from accounts.validators import PASSWORD_LENGTH


def test_provisional_password_has_required_length_and_alphabet() -> None:
    for _ in range(50):
        password = generate_provisional_password()
        assert len(password) == PASSWORD_LENGTH
        assert set(password) <= set(PROVISIONAL_ALPHABET)
