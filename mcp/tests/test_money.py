import pytest

from limafinance.lib.errors import DomainError
from limafinance.lib.money import format_brl, parse_amount_to_cents


@pytest.mark.parametrize(
    ("value", "cents"),
    [
        ("45,90", 4590),
        ("R$ 1.234,56", 123456),
        ("1.234", 123400),  # milhar pt-BR
        ("1.234.567,8", 123456780),
        ("12.50", 1250),
        ("1,234.56", 123456),
        ("0,01", 1),
        ("100", 10000),
        ("R$ 50", 5000),
        (19.99, 1999),
        (0.1, 10),
        (1234.5, 123450),
        (7, 700),
    ],
)
def test_parse(value, cents):
    assert parse_amount_to_cents(value) == cents


@pytest.mark.parametrize("value", ["-10", "0", "0,00", "abc", "1,2,3", "12,345", "1.23.4", "", 0, -5, 1.234, float("nan"), True])
def test_rejects(value):
    with pytest.raises(DomainError):
        parse_amount_to_cents(value)


def test_format_brl():
    assert format_brl(123456) == "R$ 1.234,56"
    assert format_brl(5) == "R$ 0,05"
    assert format_brl(-100000) == "-R$ 1.000,00"
    assert format_brl(123456789) == "R$ 1.234.567,89"
