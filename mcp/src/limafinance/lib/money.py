"""Dinheiro em centavos inteiros. Nada de float em string: o parse é feito por dígitos."""

import math
import re

from .errors import DomainError

_MAX_CENTS = 100_000_000_000  # R$ 1 bilhão: teto de sanidade


def parse_amount_to_cents(value: str | int | float) -> int:
    """Converte valor monetário em centavos.

    Aceita: 12.5 (número, máx. 2 casas) | "12,50" | "1.234,56" | "R$ 1.234,56" (pt-BR)
            | "12.50" | "1,234.56" (en) | "1.234" → 123400 (milhar pt-BR).
    Rejeita zero, negativos (o sinal vem de `kind`) e mais de 2 casas decimais.
    """
    if isinstance(value, bool):
        raise DomainError("invalid_amount", f"Valor inválido: {value}.")
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            raise DomainError("invalid_amount", f"Valor inválido: {value}. Use um número positivo.")
        cents = round(value * 100)
        if abs(value * 100 - cents) > 1e-6:
            raise DomainError("invalid_amount", f"Valor {value} tem mais de 2 casas decimais.")
        return _assert_safe(cents, str(value))

    raw = value
    s = re.sub(r"R\$|\s| ", "", value)
    if s.startswith("-"):
        raise DomainError(
            "invalid_amount",
            f'Valor negativo "{raw}": informe o valor positivo e use kind="expense" ou "income".',
        )
    s = s.removeprefix("+")
    if not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        raise _bad(raw)

    last_comma, last_dot = s.rfind(","), s.rfind(".")
    dec = ""
    if last_comma >= 0 and last_dot >= 0:
        # Os dois presentes: o último é o separador decimal; o outro é milhar.
        dec_sep = "," if last_comma > last_dot else "."
        thou_sep = "." if dec_sep == "," else ","
        int_part, _, dec = s.partition(dec_sep)
        if thou_sep in dec or dec_sep in dec or not _is_grouped(int_part, thou_sep):
            raise _bad(raw)
        int_part = int_part.replace(thou_sep, "")
    elif last_comma >= 0:
        parts = s.split(",")
        if len(parts) != 2:
            raise _bad(raw)
        int_part, dec = parts
    elif last_dot >= 0:
        # Só ponto: milhar pt-BR se todos os grupos têm 3 dígitos ("1.234"); senão decimal ("12.50").
        if _is_grouped(s, "."):
            int_part = s.replace(".", "")
        else:
            parts = s.split(".")
            if len(parts) != 2:
                raise _bad(raw)
            int_part, dec = parts
    else:
        int_part = s

    int_part = int_part or "0"
    if not int_part.isdigit() or (dec and not dec.isdigit()):
        raise _bad(raw)
    if len(dec) > 2:
        raise DomainError("invalid_amount", f'Valor "{raw}" tem mais de 2 casas decimais.')
    cents = int(int_part) * 100 + int(dec.ljust(2, "0") or "0")
    if cents <= 0:
        raise DomainError("invalid_amount", f'Valor "{raw}" precisa ser maior que zero.')
    return _assert_safe(cents, raw)


def format_brl(cents: int) -> str:
    """123456 → "R$ 1.234,56" """
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    integer = f"{a // 100:,}".replace(",", ".")
    return f"{sign}R$ {integer},{a % 100:02d}"


def _is_grouped(s: str, sep: str) -> bool:
    if sep not in s:
        return s.isdigit()
    return re.fullmatch(rf"\d{{1,3}}(\{sep}\d{{3}})+", s) is not None


def _bad(raw: str) -> DomainError:
    return DomainError("invalid_amount", f'Não entendi o valor "{raw}". Exemplos: "45,90", "1.234,56".')


def _assert_safe(cents: int, raw: str) -> int:
    if cents > _MAX_CENTS:
        raise DomainError("invalid_amount", f'Valor "{raw}" fora do limite aceito.')
    return cents
