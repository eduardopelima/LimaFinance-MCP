"""Datas como strings ISO: dia "YYYY-MM-DD", competência "YYYY-MM".

NUNCA use date.today() / datetime.now() sem fuso para "hoje": o Worker roda em UTC. Use today_in(tz).
"""

import calendar
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import DomainError

DEFAULT_TZ = "America/Sao_Paulo"


def today_in(tz: str = DEFAULT_TZ, now: datetime | None = None) -> str:
    """Data civil "hoje" no fuso informado. `now` (aware) injetável para testes."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(tz)).date().isoformat()


def month_of(iso_date: str) -> str:
    return iso_date[:7]


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def add_days(iso_date: str, days: int) -> str:
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


def add_months(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


RECURRENCE_HORIZON = 12  # meses gerados à frente, contando o corrente


def recurrence_months(
    start: str, end: str | None, current: str, *, from_month: str | None = None, horizon: int = RECURRENCE_HORIZON
) -> list[str]:
    """Competências que uma regra deve ter geradas: do mês corrente (ou de `from_month`, para backfill)
    até corrente + horizon - 1, limitado a [start, end]. Meses no passado só entram com `from_month`."""
    lo = max(start, from_month if from_month and from_month < current else current)
    hi = add_months(current, horizon - 1)
    if end and end < hi:
        hi = end
    out: list[str] = []
    m = lo
    while m <= hi:
        out.append(m)
        m = add_months(m, 1)
    return out


def clamp_day(month: str, day: int) -> str:
    """Dia `day` do mês limitado ao último dia (31 em fevereiro → 28/29)."""
    y, m = map(int, month.split("-"))
    return f"{month}-{min(day, days_in_month(y, m)):02d}"


def is_valid_iso_date(s: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def is_valid_month(s: str) -> bool:
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    return bool(m) and 1 <= int(m.group(2)) <= 12


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn")


def parse_date(value: str, today: str) -> str:
    """ "hoje" | "ontem" | "anteontem" | "DD/MM/AAAA" | "DD/MM/AA" | "DD/MM" (ano corrente) | "YYYY-MM-DD" → ISO."""
    s = _fold(value)
    if s in ("hoje", "today"):
        return today
    if s in ("ontem", "yesterday"):
        return add_days(today, -1)
    if s == "anteontem":
        return add_days(today, -2)

    iso = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        iso = s
    elif m := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?", s):
        d, mo, y = m.group(1), m.group(2), m.group(3) or today[:4]
        if len(y) == 2:
            y = "20" + y
        iso = f"{y}-{int(mo):02d}-{int(d):02d}"
    if not iso or not is_valid_iso_date(iso):
        raise DomainError("invalid_date", f'Data inválida "{value}". Use DD/MM/AAAA (ex.: 05/03/2025), "hoje" ou "ontem".')
    return iso


_MONTHS_PT = {
    "jan": 1, "janeiro": 1, "fev": 2, "fevereiro": 2, "mar": 3, "marco": 3, "abr": 4, "abril": 4,
    "mai": 5, "maio": 5, "jun": 6, "junho": 6, "jul": 7, "julho": 7, "ago": 8, "agosto": 8,
    "set": 9, "setembro": 9, "out": 10, "outubro": 10, "nov": 11, "novembro": 11, "dez": 12, "dezembro": 12,
}


def parse_month(value: str, today: str) -> str:
    """ "YYYY-MM" | "MM/AAAA" | "MM/AA" | "dez/23" | "dezembro de 2023" | "este mês" | "mês passado" | "próximo mês" → "YYYY-MM"."""
    s = _fold(value)
    current = month_of(today)
    if s in ("atual", "este mes", "esse mes", "mes atual", "current"):
        return current
    if s in ("mes passado", "ultimo mes", "anterior"):
        return add_months(current, -1)
    if s in ("proximo mes", "mes que vem"):
        return add_months(current, 1)

    out = None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})", s):
        out = f"{m.group(1)}-{int(m.group(2)):02d}"
    elif m := re.fullmatch(r"(\d{1,2})[/.-](\d{2}|\d{4})", s):
        y = m.group(2) if len(m.group(2)) == 4 else "20" + m.group(2)
        out = f"{y}-{int(m.group(1)):02d}"
    elif m := re.fullmatch(r"([a-z]+)\.?(?:\s*(?:/|-|de)\s*|\s+)(\d{2}|\d{4})", s):
        mo = _MONTHS_PT.get(m.group(1))
        y = m.group(2) if len(m.group(2)) == 4 else "20" + m.group(2)
        if mo:
            out = f"{y}-{mo:02d}"
    if not out or not is_valid_month(out):
        raise DomainError("invalid_month", f'Mês de competência inválido "{value}". Use MM/AAAA (ex.: 03/2025) ou "mês passado".')
    return out
