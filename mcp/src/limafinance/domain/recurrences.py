from ..db import DB, changes
from ..lib.dates import clamp_day, month_of, parse_month, today_in
from ..lib.errors import DomainError
from ..lib.ids import new_id
from ..lib.money import format_brl, parse_amount_to_cents
from .categories import Kind, load_categories, resolve_category
from .context import Actor, audit_stmt, now_iso


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
) -> dict:
    today = today_in(actor.timezone)
    cat = resolve_category(await load_categories(db, actor.user_id), category, kind)
    cents = parse_amount_to_cents(amount)
    start = parse_month(start_month, today) if start_month else month_of(today)
    end = parse_month(end_month, today) if end_month else None
    if end and end < start:
        raise DomainError("invalid_range", "end_month é anterior a start_month.")
    rid = new_id("rec")
    await db.batch([
        db.stmt(
            """INSERT INTO recurrences (id, user_id, kind, amount_cents, category_id, description, note, day_of_month, start_month, end_month)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rid, actor.user_id, kind, cents, cat.id, description.strip(), (note or "").strip() or None, day_of_month, start, end,
        ),
        audit_stmt(db, actor, "recurrence.create", rid, {"category": cat.path, "amount_cents": cents, "start": start, "end": end}),
    ])
    current = month_of(today)
    created = await materialize_month(db, actor.user_id, current, only_id=rid) if start <= current and (not end or end >= current) else 0
    out = {
        "id": rid, "category": cat.path, "valor": format_brl(cents), "start_month": start, "end_month": end,
        "materialized_current_month": created > 0,
    }
    if start < current:
        out["note"] = "Meses anteriores a este NÃO foram gerados (evita duplicar o histórico importado)."
    return out


async def list_recurrences(db: DB, actor: Actor, include_ended: bool = False) -> list[dict]:
    current = month_of(today_in(actor.timezone))
    sql = """SELECT r.id, r.kind, r.amount_cents, r.description, r.note, r.day_of_month, r.start_month, r.end_month,
                    CASE WHEN p.name IS NULL THEN c.name ELSE p.name || ' > ' || c.name END AS category
             FROM recurrences r JOIN categories c ON c.id = r.category_id LEFT JOIN categories p ON p.id = c.parent_id
             WHERE r.user_id = ? AND r.deleted_at IS NULL"""
    args = [actor.user_id]
    if not include_ended:
        sql += " AND (r.end_month IS NULL OR r.end_month >= ?)"
        args.append(current)
    rows = await db.all(sql + " ORDER BY r.kind, r.day_of_month", *args)
    return [{**r, "valor": format_brl(r["amount_cents"])} for r in rows]


async def end_recurrence(db: DB, actor: Actor, rec_id: str, last_month: str | None = None) -> dict:
    """Encerra a regra (não apaga lançamentos já gerados). Padrão: último mês = mês corrente."""
    today = today_in(actor.timezone)
    row = await db.first(
        "SELECT id, start_month FROM recurrences WHERE user_id = ? AND id = ? AND deleted_at IS NULL", actor.user_id, rec_id
    )
    if not row:
        raise DomainError("not_found", f"Recorrência {rec_id} não encontrada.")
    last = parse_month(last_month, today) if last_month else month_of(today)
    if last < row["start_month"]:  # nunca valeu: soft delete
        stmt = db.stmt("UPDATE recurrences SET deleted_at = ?, updated_at = ? WHERE user_id = ? AND id = ?", now_iso(), now_iso(), actor.user_id, rec_id)
    else:
        stmt = db.stmt("UPDATE recurrences SET end_month = ?, updated_at = ? WHERE user_id = ? AND id = ?", last, now_iso(), actor.user_id, rec_id)
    await db.batch([stmt, audit_stmt(db, actor, "recurrence.end", rec_id, {"last_month": last})])
    return {"id": rec_id, "last_month": last}


async def materialize_month(db: DB, user_id: str, month: str, only_id: str | None = None) -> int:
    """Gera os lançamentos de um mês a partir das regras ativas. Idempotente:
    idempotency_key = "rec:<id>:<YYYY-MM>" + ON CONFLICT DO NOTHING. Seguro para retry da Queue."""
    sql = """SELECT id, kind, amount_cents, category_id, description, note, day_of_month FROM recurrences
             WHERE user_id = ? AND deleted_at IS NULL AND start_month <= ? AND (end_month IS NULL OR end_month >= ?)"""
    args = [user_id, month, month]
    if only_id:
        sql += " AND id = ?"
        args.append(only_id)
    rules = await db.all(sql, *args)
    if not rules:
        return 0
    results = await db.batch([
        db.stmt(
            """INSERT INTO transactions (id, user_id, kind, amount_cents, occurred_on, competence_month, category_id,
                 description, note, recurrence_id, source, client, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'recurrence', 'job:recurrences', ?)
               ON CONFLICT (user_id, idempotency_key) DO NOTHING""",
            new_id("txn"), user_id, r["kind"], r["amount_cents"], clamp_day(month, int(r["day_of_month"])), month,
            r["category_id"], r["description"], r["note"], r["id"], f"rec:{r['id']}:{month}",
        )
        for r in rules
    ])
    return sum(changes(res) for res in results)
