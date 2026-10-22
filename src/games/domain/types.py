"""Domain vocabulary shared by the list engine. Values match the database choices."""

from enum import StrEnum


class PlayerType(StrEnum):
    MENSALISTA = "mensalista"
    AVULSO = "avulso"
    AGREGADO = "agregado"


class Position(StrEnum):
    LINHA = "linha"
    GOLEIRO = "goleiro"


class EntryStatus(StrEnum):
    LISTA = "lista"  # holds a spot for Sunday
    ESPERA = "espera"  # on the waitlist
    SAIU = "saiu"  # dropped out, declined or removed
