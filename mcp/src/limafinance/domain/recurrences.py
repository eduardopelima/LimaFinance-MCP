"""Recorrência = regra que gera lançamentos reais (chave `rec:<id>:<YYYY-MM>`), com até 12 meses de antecedência.

- A regra guarda o valor "padrão". Exceções pontuais (rateio, mês pulado) são edições/exclusões dos lançamentos
  gerados: a chave fica ocupada, então a geração nunca recria nem sobrescreve.
- Editar à mão um lançamento gerado marca `recurrence_override = 1`; reajuste e encerramento não mexem nele.
- Reajuste (update_recurrence) muda a regra E os lançamentos já gerados a partir de `from_month`.
"""

import json

from ..db import DB, Stmt, changes
from ..lib.dates import RECURRENCE_HORIZON, add_months, clamp_day, month_of, parse_month, recurrence_months, today_in
from ..lib.errors import DomainError
from ..lib.ids import new_id
from ..lib.money import format_brl, parse_amount_to_cents
from .categories import Kind, load_categories, resolve_category
from .context import Actor, audit_stmt, now_iso

# Um statement por lote de regras (json_each): o D1 limita queries por invocação e parâmetros por query.
_MATERIALIZE_SQL = """
INSERT INTO transactions (id, user_id, kind, amount_cents, occurred_on, competence_month, category_id,
  description, note, recurrence_id, source, client, idempotency_key)
SELECT json_extract(j.value, '$.id'), r.user_id, r.kind, r.amount_cents, json_extract(j.value, '$.d'),
  json_extract(j.value, '$.m'), r.category_id, r.description, r.note, r.id, 'recurrence', 'job:recurrences',
  'rec:' || r.id || ':' || json_extract(j.value, '$.m')
FROM json_each(?) j
JOIN recurrences r ON r.id = json_extract(j.value, '$.r') AND r.user_id = ? AND r.deleted_at IS NULL
WHERE true
ON CONFLICT (user_id, idempotency_key) DO NOTHING"""

# Dia `day` limitado ao último dia da competência (mesma regra de lib.dates.clamp_day, em SQL).
_CLAMPED_DAY_SQL = """competence_month || '-' || printf('%02d', min(?, CAST(strftime('%d',
  date(competence_month || '-01', '+1 month', '-1 day')) AS INTEGER)))"""


def _items(rule_id: str, day_of_month: int, months: list[str]) -> list[dict]:
    return [{"id": new_id("txn"), "r": rule_id, "m": m, "d": clamp_day(m, day_of_month)} for m in months]


def _materialize_stmt(db: DB, user_id: str, items: list[dict]) -> Stmt:
    return db.stmt(_MATERIALIZE_SQL, json.dumps(items), user_id)


async def materialize(db: DB, user_id: str, current_month: str, only_id: str | None = None) -> int:
    """Garante os lançamentos do mês corrente até corrente + 11 de todas as regras ativas. Idempotente
    (ON CONFLICT DO NOTHING na chave), seguro para retry da Queue. Retorna quantos lançamentos criou."""
    sql = """SELECT id, day_of_month, start_month, end_month FROM recurrences
             WHERE user_id = ? AND deleted_at IS NULL AND start_month <= ? AND (end_month IS NULL OR end_month >= ?)"""
    args: list = [user_id, add_months(current_month, RECURRENCE_HORIZON - 1), current_month]
    if only_id:
        sql += " AND id = ?"
        args.append(only_id)
    items = [
        it
        for r in await db.all(sql, *args)
        for it in _items(r["id"], int(r["day_of_month"]), recurrence_months(r["start_month"], r["end_month"], current_month))
    ]
    created = 0
    for i in range(0, len(items), 500):
        created += await db.run(_MATERIALIZE_SQL, json.dumps(items[i : i + 500]), user_id)
    return created


def _span(months: list[str]) -> str | None:
    return f"{months[0]} a {months[-1]}" if months else None


async def create_recurrence(
    db: DB,
    actor: Actor,
    *,
    kind: Kind,
    amount: str | int | float,
    category: str,
    description: str,
    day_of_month: int,
    start_month: str | None = None,
    end_month: str | None = None,
    note: str | None = None,
    include_past_months: bool = False,
) -> dict:
    today = today_in(actor.timezone)
    current = month_of(today)
    cat = resolve_category(await load_categories(db, actor.user_id), category, kind)
    cents = parse_amount_to_cents(amount)
    start = parse_month(start_month, today) if start_month else current
    end = parse_month(end_month, today) if end_month else None
    if end and end < start:
        raise DomainError("invalid_range", "end_month é anterior a start_month.")
    description = description.strip()
    if not description:
        raise DomainError("description_required", "Descrição é obrigatória.")
    rid = new_id("rec")
    months = recurrence_months(start, end, current, from_month=start if include_past_months else None)
    stmts = [
        db.stmt(
            """INSERT INTO recurrences (id, user_id, kind, amount_cents, category_id, description, note, day_of_month, start_month, end_month)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rid, actor.user_id, kind, cents, cat.id, description, (note or "").strip() or None, day_of_month, start, end,
        ),
        audit_stmt(db, actor, "recurrence.create", rid, {
            "category": cat.path, "amount_cents": cents, "start": start, "end": end, "generated": months,
        }),
    ]
    if months:  # mesmo batch: a regra e os lançamentos entram juntos (ou nada entra)
        stmts.append(_materialize_stmt(db, actor.user_id, _items(rid, day_of_month, months)))
    await db.batch(stmts)

    out = {
        "id": rid, "category": cat.path, "valor": format_brl(cents), "day_of_month": day_of_month,
        "start_month": start, "end_month": end, "generated_count": len(months), "generated_months": _span(months),
    }
    if start < current and not include_past_months:
        out["note"] = (f"Meses de {start} a {add_months(current, -1)} NÃO foram gerados (podem já estar lançados). "
                       "Para gerá-los, crie com include_past_months=true.")
    if not end and months:
        out["horizon"] = f"Sem data fim: os próximos {RECURRENCE_HORIZON} meses ficam gerados; um mês novo entra a cada virada de mês."
    return out


async def list_recurrences(db: DB, actor: Actor, include_ended: bool = False) -> list[dict]:
    current = month_of(today_in(actor.timezone))
    sql = """SELECT r.id, r.kind, r.amount_cents, r.description, r.note, r.day_of_month, r.start_month, r.end_month,
                    CASE WHEN p.name IS NULL THEN c.name ELSE p.name || ' > ' || c.name END AS category,
                    (SELECT MAX(t.competence_month) FROM transactions t
                      WHERE t.user_id = r.user_id AND t.recurrence_id = r.id AND t.deleted_at IS NULL) AS generated_until
             FROM recurrences r JOIN categories c ON c.id = r.category_id LEFT JOIN categories p ON p.id = c.parent_id
             WHERE r.user_id = ? AND r.deleted_at IS NULL"""
    args = [actor.user_id]
    if not include_ended:
        sql += " AND (r.end_month IS NULL OR r.end_month >= ?)"
        args.append(current)
    rows = await db.all(sql + " ORDER BY r.kind, r.day_of_month", *args)
    return [{**r, "valor": format_brl(r["amount_cents"])} for r in rows]


async def _get_rule(db: DB, actor: Actor, rec_id: str) -> dict:
    row = await db.first(
        """SELECT id, kind, amount_cents, category_id, description, day_of_month, start_month, end_month FROM recurrences
           WHERE user_id = ? AND id = ? AND deleted_at IS NULL""",
        actor.user_id, rec_id,
    )
    if not row:
        raise DomainError("not_found", f"Recorrência {rec_id} não encontrada.")
    return row


async def _edited(db: DB, actor: Actor, rec_id: str, after_or_from: str, *, strict: bool) -> list[dict]:
    """Lançamentos gerados e editados à mão (override) a partir de um mês: ficam intocados, e o LLM avisa o usuário."""
    rows = await db.all(
        f"""SELECT id, competence_month, amount_cents, description FROM transactions
            WHERE user_id = ? AND recurrence_id = ? AND deleted_at IS NULL AND recurrence_override = 1
              AND competence_month {'>' if strict else '>='} ? ORDER BY competence_month""",
        actor.user_id, rec_id, after_or_from,
    )
    return [{"id": r["id"], "month": r["competence_month"], "valor": format_brl(r["amount_cents"]),
             "description": r["description"]} for r in rows]


async def update_recurrence(
    db: DB,
    actor: Actor,
    rec_id: str,
    *,
    from_month: str | None = None,
    amount: str | int | float | None = None,
    category: str | None = None,
    description: str | None = None,
    day_of_month: int | None = None,
) -> dict:
    """Reajuste/alteração PERMANENTE a partir de `from_month` (padrão: mês corrente; pode ser no passado).
    Atualiza a regra e os lançamentos gerados com competência >= from_month que não foram editados à mão."""
    today = today_in(actor.timezone)
    current = month_of(today)
    rule = await _get_rule(db, actor, rec_id)
    frm = parse_month(from_month, today) if from_month else current
    frm = max(frm, rule["start_month"])
    if rule["end_month"] and frm > rule["end_month"]:
        raise DomainError("invalid_range", f"A recorrência termina em {rule['end_month']}; from_month {frm} é depois disso.")
    horizon_end = add_months(current, RECURRENCE_HORIZON - 1)
    if frm > horizon_end:
        # A regra só guarda um valor: um reajuste além do horizonte faria os meses intermediários nascerem com o valor novo.
        raise DomainError("too_far_ahead", f"Só é possível reajustar até {horizon_end} (12 meses à frente). Peça de novo mais perto da data.")

    rule_sets: list[str] = []
    txn_sets: list[str] = []
    rule_args: list = []
    txn_args: list = []
    changed: dict = {}
    if amount is not None:
        cents = parse_amount_to_cents(amount)
        rule_sets.append("amount_cents = ?"); rule_args.append(cents)
        txn_sets.append("amount_cents = ?"); txn_args.append(cents)
        changed["amount_cents"] = cents
    if category is not None:
        cat = resolve_category(await load_categories(db, actor.user_id), category, rule["kind"])
        rule_sets.append("category_id = ?"); rule_args.append(cat.id)
        txn_sets.append("category_id = ?"); txn_args.append(cat.id)
        changed["category"] = cat.path
    if description is not None:
        d = description.strip()
        if not d:
            raise DomainError("description_required", "Descrição não pode ficar vazia.")
        rule_sets.append("description = ?"); rule_args.append(d)
        txn_sets.append("description = ?"); txn_args.append(d)
        changed["description"] = d
    if day_of_month is not None:
        rule_sets.append("day_of_month = ?"); rule_args.append(day_of_month)
        txn_sets.append(f"occurred_on = {_CLAMPED_DAY_SQL}"); txn_args.append(day_of_month)
        changed["day_of_month"] = day_of_month
    if not changed:
        raise DomainError("nothing_to_update", "Nenhum campo para alterar (amount, category, description ou day_of_month).")

    skipped = await _edited(db, actor, rec_id, frm, strict=False)
    at = now_iso()
    results = await db.batch([
        db.stmt(f"UPDATE recurrences SET {', '.join(rule_sets)}, updated_at = ? WHERE user_id = ? AND id = ?",
                *rule_args, at, actor.user_id, rec_id),
        db.stmt(
            f"""UPDATE transactions SET {', '.join(txn_sets)}, updated_at = ?
                WHERE user_id = ? AND recurrence_id = ? AND competence_month >= ? AND deleted_at IS NULL AND recurrence_override = 0""",
            *txn_args, at, actor.user_id, rec_id, frm,
        ),
        audit_stmt(db, actor, "recurrence.update", rec_id, {"from_month": frm, "before": rule, "changes": changed}),
    ])
    out = {"id": rec_id, "from_month": frm, "changes": changed, "updated_transactions": changes(results[1])}
    if "amount_cents" in changed:
        out["valor"] = format_brl(changed["amount_cents"])
    if skipped:
        out["kept_edited"] = skipped
        out["note"] = "Estes meses foram editados à mão e NÃO foram alterados. Pergunte ao usuário se devem seguir o novo valor."
    return out


async def end_recurrence(db: DB, actor: Actor, rec_id: str, last_month: str | None = None) -> dict:
    """Encerra a regra e exclui (soft delete) os lançamentos gerados DEPOIS do último mês. Padrão: último mês = corrente."""
    today = today_in(actor.timezone)
    row = await _get_rule(db, actor, rec_id)
    last = parse_month(last_month, today) if last_month else month_of(today)
    at = now_iso()
    if last < row["start_month"]:  # nunca valeu: soft delete da regra
        rule_stmt = db.stmt("UPDATE recurrences SET deleted_at = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                            at, at, actor.user_id, rec_id)
    else:
        rule_stmt = db.stmt("UPDATE recurrences SET end_month = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                            last, at, actor.user_id, rec_id)
    kept = await _edited(db, actor, rec_id, last, strict=True)
    results = await db.batch([
        rule_stmt,
        db.stmt(
            """UPDATE transactions SET deleted_at = ?, updated_at = ?
               WHERE user_id = ? AND recurrence_id = ? AND competence_month > ? AND deleted_at IS NULL AND recurrence_override = 0""",
            at, at, actor.user_id, rec_id, last,
        ),
        audit_stmt(db, actor, "recurrence.end", rec_id, {"last_month": last}),
    ])
    out = {"id": rec_id, "last_month": last, "removed_future_transactions": changes(results[1])}
    if kept:
        out["kept_edited"] = kept
        out["note"] = "Estes lançamentos depois do último mês foram editados à mão e foram mantidos. Pergunte se devem ser excluídos."
    return out
