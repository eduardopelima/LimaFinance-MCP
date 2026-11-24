from datetime import datetime, timezone

import pytest

from limafinance.lib.dates import add_months, clamp_day, parse_date, parse_month, today_in
from limafinance.lib.errors import DomainError


def test_today_in_sao_paulo_not_utc():
    # 2025-03-01 02:30 UTC = 2025-02-28 23:30 em São Paulo (UTC-3)
    assert today_in("America/Sao_Paulo", datetime(2025, 3, 1, 2, 30, tzinfo=timezone.utc)) == "2025-02-28"
    assert today_in("America/Sao_Paulo", datetime(2025, 3, 1, 3, 0, tzinfo=timezone.utc)) == "2025-03-01"


@pytest.mark.parametrize(
    ("value", "out"),
    [
        ("hoje", "2025-03-01"),
        ("Ontem", "2025-02-28"),
        ("anteontem", "2025-02-27"),
        ("05/03/2025", "2025-03-05"),
        ("5/3/25", "2025-03-05"),
        ("05/03", "2025-03-05"),
        ("2024-12-31", "2024-12-31"),
        ("29/02/2024", "2024-02-29"),
    ],
)
def test_parse_date(value, out):
    assert parse_date(value, "2025-03-01") == out


@pytest.mark.parametrize("value", ["31/02/2025", "2025-13-01", "amanhã talvez", "29/02/2025"])
def test_parse_date_rejects(value):
    with pytest.raises(DomainError):
        parse_date(value, "2025-03-01")


@pytest.mark.parametrize(
    ("value", "out"),
    [
        ("2023-12", "2023-12"),
        ("12/2023", "2023-12"),
        ("12/23", "2023-12"),
        ("dez/23", "2023-12"),
        ("Dezembro/2023", "2023-12"),
        ("dezembro de 2023", "2023-12"),
        ("março 2024", "2024-03"),
        ("este mês", "2025-01"),
        ("mês passado", "2024-12"),
        ("próximo mês", "2025-02"),
    ],
)
def test_parse_month(value, out):
    assert parse_month(value, "2025-01-15") == out


@pytest.mark.parametrize("value", ["13/2024", "foo/2024", ""])
def test_parse_month_rejects(value):
    with pytest.raises(DomainError):
        parse_month(value, "2025-01-15")


def test_helpers():
    assert add_months("2023-12", 1) == "2024-01"
    assert add_months("2024-01", -1) == "2023-12"
    assert clamp_day("2025-02", 31) == "2025-02-28"
    assert clamp_day("2024-02", 30) == "2024-02-29"
    assert clamp_day("2025-04", 5) == "2025-04-05"


# ---------- janela de recorrência (12 meses à frente) ----------
from limafinance.lib.dates import recurrence_months  # noqa: E402


def test_recurrence_window_no_end_is_12_months_from_current():
    ms = recurrence_months("2025-01", None, "2026-11")
    assert ms[0] == "2026-11" and ms[-1] == "2027-10" and len(ms) == 12


def test_recurrence_window_future_start_and_end():
    assert recurrence_months("2027-03", None, "2026-11") == ["2027-03", "2027-04", "2027-05", "2027-06", "2027-07", "2027-08", "2027-09", "2027-10"]
    assert recurrence_months("2026-12", "2027-02", "2026-11") == ["2026-12", "2027-01", "2027-02"]
    assert recurrence_months("2028-01", None, "2026-11") == []  # além do horizonte: entra quando a janela chegar
    assert recurrence_months("2025-01", "2026-10", "2026-11") == []  # já terminou


def test_recurrence_window_backfill_only_with_from_month():
    assert recurrence_months("2026-09", None, "2026-11")[0] == "2026-11"
    ms = recurrence_months("2026-09", None, "2026-11", from_month="2026-09")
    assert ms[:3] == ["2026-09", "2026-10", "2026-11"] and ms[-1] == "2027-10"
