"""Parser da planilha antiga (export CSV) → registros normalizados. Puro, sem I/O (testado em tests/test_planilha.py).

Colunas (cabeçalho, ordem livre, acentos/maiúsculas ignorados):
  Mês-Competência | Data | Categoria | Subcategoria | Descrição | Ocorrência | Valor | Provento | Meta Gasto | Nota
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field

from limafinance.lib.errors import DomainError
from limafinance.lib.dates import is_valid_iso_date, month_of, parse_date, parse_month
from limafinance.lib.money import parse_amount_to_cents
from limafinance.lib.text import normalize_key


@dataclass
class ParsedTxn:
    line: int
    kind: str
    amount_cents: int
    occurred_on: str
    competence_month: str
    category: str
    subcategory: str | None
    description: str
    note: str | None
    monthly: bool
    idempotency_key: str


@dataclass
class ParseResult:
    transactions: list[ParsedTxn] = field(default_factory=list)
    income_by_month: dict[str, int] = field(default_factory=dict)  # coluna Provento (1 valor/mês)
    budget_by_month: dict[str, int] = field(default_factory=dict)  # coluna Meta Gasto (teto global)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_zero: int = 0
    skipped_uncategorized: int = 0
    # competência > until: não vão para transactions (ex.: planejamento); Mensais viram candidatas a recorrência
    deferred: list[ParsedTxn] = field(default_factory=list)


COLUMNS = {
    "competence": ["mes-competencia", "mes competencia", "competencia", "mes"],
    "date": ["data"],
    "category": ["categoria"],
    "subcategory": ["subcategoria", "sub-categoria", "sub categoria"],
    "description": ["descricao"],
    "occurrence": ["ocorrencia"],
    "amount": ["valor"],
    "income": ["provento", "proventos", "renda"],
    "budget": ["meta gasto", "meta de gasto", "meta", "teto"],
    "note": ["nota", "notas", "obs", "observacao", "observacoes", "nota livre"],
}
REQUIRED = ["competence", "category", "description", "amount"]


def parse_csv(text: str) -> list[list[str]]:
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ("\t" if "\t" in first else ",")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return [r for r in rows if any(c.strip() for c in r)]


def _map_header(header: list[str]) -> tuple[dict[str, int], list[str]]:
    mapping: dict[str, int] = {}
    for i, h in enumerate(header):
        key = re.sub(r"\s*-\s*", "-", re.sub(r"[_.]", " ", normalize_key(h)))
        for col, aliases in COLUMNS.items():
            if col not in mapping and key in aliases:
                mapping[col] = i
    return mapping, [c for c in REQUIRED if c not in mapping]


def _month_from(raw: str, today: str) -> str:
    # Sheets às vezes exporta a competência como data completa (01/12/2023).
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", raw.strip()):
        return month_of(parse_date(raw, today))
    return parse_month(raw, today)


_ZERO = re.compile(r"^r?\$?\s*0+([.,]0+)?$", re.I)


def parse_planilha(
    text: str,
    *,
    today: str,
    income_categories: list[str] | None = None,
    default_category: str | None = None,
    skip_uncategorized: bool = False,
    until: str | None = None,
) -> ParseResult:
    out = ParseResult()
    rows = parse_csv(text)
    if len(rows) < 2:
        out.errors.append("CSV vazio ou sem linhas de dados.")
        return out
    mapping, missing = _map_header(rows[0])
    if missing:
        out.errors.append(f"Colunas obrigatórias ausentes: {', '.join(missing)}. Cabeçalho lido: {' | '.join(rows[0])}")
        return out
    income_keys = {normalize_key(c) for c in (income_categories or [])}

    def get(r: list[str], col: str) -> str:
        i = mapping.get(col)
        return r[i].strip() if i is not None and i < len(r) else ""

    seen: dict[str, int] = {}
    month_values: dict[str, dict[str, set[int]]] = {"income": {}, "budget": {}}

    for idx, r in enumerate(rows[1:]):
        line = idx + 2
        try:
            month = _month_from(get(r, "competence"), today)
            extra_notes: list[str] = []
            for col, label in (("income", "Provento"), ("budget", "Meta Gasto")):  # valores do MÊS (repetidos nas linhas)
                raw = get(r, col)
                if raw and raw != "-" and not _ZERO.match(raw):
                    try:
                        cents = parse_amount_to_cents(raw.lstrip("-"))  # parse ANTES: valor inválido não deixa mês vazio
                    except DomainError:
                        # anotação solta na coluna de valor (ex.: "A FECHAR"): não é valor; vira nota da linha
                        out.warnings.append(f'Linha {line}: "{raw}" na coluna {label} não é valor; guardado na nota.')
                        extra_notes.append(f"{label}: {raw}")
                        continue
                    month_values[col].setdefault(month, set()).add(cents)

            raw_amount = get(r, "amount")
            if not raw_amount:
                continue  # linha só com Provento/Meta
            if _ZERO.match(raw_amount.replace(" ", "")):
                out.warnings.append(f"Linha {line}: valor zero ({get(r, 'description') or get(r, 'category')}) ignorado.")
                out.skipped_zero += 1
                continue
            negative = bool(re.match(r"^\s*-|\(", raw_amount))
            cents = parse_amount_to_cents(re.sub(r"[()]", "", re.sub(r"^\s*-\s*", "", raw_amount)))
            category = get(r, "category") or (default_category or "")
            if not category:
                if skip_uncategorized:
                    out.warnings.append(f"Linha {line}: sem categoria ({get(r, 'description')}) ignorada.")
                    out.skipped_uncategorized += 1
                    continue
                raise ValueError("Categoria vazia (use --default-category ou --skip-uncategorized)")
            kind = "income" if normalize_key(category) in income_keys else "expense"
            if negative:
                out.warnings.append(f'Linha {line}: valor negativo "{raw_amount}" importado como {kind} de {cents / 100:.2f}. Confira.')

            raw_date = get(r, "date")
            occurred = parse_date(raw_date, today) if raw_date else f"{month}-01"
            if not is_valid_iso_date(occurred):
                occurred = f"{month}-01"
            sub = get(r, "subcategory") or None
            description = get(r, "description") or sub or category

            # Chave determinística: reimportar não duplica; linhas idênticas legítimas ganham ordinal.
            base = "|".join([month, occurred, normalize_key(category), normalize_key(sub or ""), normalize_key(description), str(cents), kind])
            seen[base] = seen.get(base, 0) + 1
            key = "imp:" + hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()[:32]

            out.transactions.append(ParsedTxn(
                line=line, kind=kind, amount_cents=cents, occurred_on=occurred, competence_month=month,
                category=category, subcategory=sub, description=description, note=" · ".join(filter(None, [get(r, "note"), *extra_notes])) or None,
                monthly=normalize_key(get(r, "occurrence")) == "mensal", idempotency_key=key,
            ))
        except Exception as e:
            msg = getattr(e, "message", None) or str(e)
            out.errors.append(f"Linha {line}: {msg} — [{' | '.join(r)}]")

    for col, target, label in (("income", out.income_by_month, "Provento"), ("budget", out.budget_by_month, "Meta Gasto")):
        for month, values in month_values[col].items():
            if len(values) > 1:
                vals = ", ".join(f"{v / 100:.2f}" for v in sorted(values))
                out.errors.append(f"{label} de {month} tem valores diferentes entre linhas ({vals}). Deixe um só valor por mês.")
            else:
                target[month] = next(iter(values))

    if until:
        out.deferred = [t for t in out.transactions if t.competence_month > until]
        out.transactions = [t for t in out.transactions if t.competence_month <= until]
        for target in (out.income_by_month, out.budget_by_month):
            for m in [m for m in target if m > until]:
                del target[m]
    return out
