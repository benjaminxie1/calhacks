"""Decimal-only money and quantity helpers. Floats are banned (R6)."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")


def D(x) -> Decimal:
    if isinstance(x, float):
        raise TypeError("floats are banned for money and quantities")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x)) if isinstance(x, int) else Decimal(x)


def cents(x: Decimal) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def s(x: Decimal) -> str:
    """Canonical string for hashing / files: no exponent, trailing zeros trimmed to 2dp minimum for money."""
    x = D(x)
    if x == x.to_integral_value():
        return str(x.quantize(Decimal(1)))
    return format(x.normalize(), "f")
