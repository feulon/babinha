"""How many spots each position has.

Four teams of five line players need 20 line spots. Goalkeepers are shared, up to 4. The two
spots after the 20th are flexible: line players fill them while there are fewer than two
goalkeepers (21 line with 1 goalkeeper, 22 with none). Total stays between 22 and 24 people.
"""

MAX_GOALKEEPERS = 4
FIXED_LINE_SLOTS = 20
FLEX_SLOTS = 2


def line_capacity(goalkeepers: int) -> int:
    if not 0 <= goalkeepers <= MAX_GOALKEEPERS:
        raise ValueError(f"goalkeepers must be between 0 and {MAX_GOALKEEPERS}")
    return FIXED_LINE_SLOTS + FLEX_SLOTS - min(goalkeepers, FLEX_SLOTS)


def fits(line: int, goalkeepers: int) -> bool:
    return 0 <= goalkeepers <= MAX_GOALKEEPERS and 0 <= line <= line_capacity(goalkeepers)
