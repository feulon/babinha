import pytest
from django.core.exceptions import ValidationError

from core.pix import is_valid_cnpj, is_valid_cpf, normalize_pix_key, validate_pix_key


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [
            ("529.982.247-25", "52998224725"),
            ("52998224725", "52998224725"),
            ("11.222.333/0001-81", "11222333000181"),
            ("12.ABC.345/01DE-35", "12ABC34501DE35"),
            ("+55 (11) 99999-8888", "+5511999998888"),
            ("+551133334444", "+551133334444"),
            ("  Presidente@Baba.COM.br ", "presidente@baba.com.br"),
            ("123E4567-E89B-12D3-A456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
        ],
    )
    def test_accepts_every_key_type(self, raw: str, canonical: str) -> None:
        assert normalize_pix_key(raw) == canonical

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("", "Informe a chave Pix"),
            ("529.982.247-24", "CPF inválido"),
            ("11111111111", "CPF inválido"),
            ("11 99999-8888", "Para celular, use +55"),
            ("+1 202 555 0100", "Celular inválido"),
            ("+55 11 9999", "Celular inválido"),
            ("11.222.333/0001-82", "CNPJ inválido"),
            ("nome@", "e-mail"),
            ("chave qualquer", "Chave Pix inválida"),
        ],
    )
    def test_rejects_typos(self, raw: str, message: str) -> None:
        with pytest.raises(ValidationError) as error:
            normalize_pix_key(raw)
        assert message.lower() in " ".join(error.value.messages).lower()

    def test_rejects_an_email_too_long_for_pix(self) -> None:
        with pytest.raises(ValidationError):
            normalize_pix_key(f"{'a' * 64}@{'b' * 20}.com")


class TestChecksums:
    def test_cpf(self) -> None:
        assert is_valid_cpf("52998224725")
        assert not is_valid_cpf("52998224715")
        assert not is_valid_cpf("5299822472")

    def test_cnpj_numeric_and_alphanumeric(self) -> None:
        assert is_valid_cnpj("11222333000181")
        assert is_valid_cnpj("12ABC34501DE35")
        assert not is_valid_cnpj("12ABC34501DE36")
        assert not is_valid_cnpj("00000000000000")


class TestModelValidator:
    def test_only_canonical_keys_are_stored(self) -> None:
        validate_pix_key("52998224725")
        validate_pix_key("")
        with pytest.raises(ValidationError):
            validate_pix_key("529.982.247-25")
