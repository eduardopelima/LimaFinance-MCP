from typing import Any

from pydantic import BaseModel

from ..db import DB
from ..lib.dates import month_of, parse_date, parse_month, today_in
from ..lib.errors import DomainError
from ..lib.ids import new_id
from ..lib.money import format_brl, parse_amount_to_cents
from .categories import Kind, load_categories, resolve_category
from .context import Actor, audit_stmt, now_iso


class NewTransaction(BaseModel):
    kind: Kind
    amount: str | int | float
    category: str
    description: str
    date: str | None = None
    competence_month: str | None = None
    note: str | None = None
    idempotency_key: str | None = None


_SELECT_VIEW = """
SELECT t.id, t.kind, t.amount_cents, t.occurred_on, t.competence_month, t.category_id,
       CASE WHEN p.name IS NULL THEN c.name ELSE p.name || ' > ' || c.name END AS category,
       t.description, t.note, t.recurrence_id, t.recurrence_override, t.source, t.deleted_at
FROM transactions t
JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id"""


def _view(r: dict[str, Any], today: str) -> dict[str, Any]:
    # previsto = data real ainda não chegou (ex.: recorrências geradas com antecedência).
    return {**r, "valor": format_brl(r["amount_cents"]), "previsto": r["occurred_on"] > today}


async def add_transactions(db: DB, actor: Actor, items: list[NewTransaction]) -> dict:
    """Lote atômico: entram todos ou nenhum. idempotency_key repetida devolve o lançamento existente."""
    if not items:
        raise DomainError("empty_batch", "Nenhum lançamento informado.")
    today = today_in(actor.timezone)
    categories = await load_categories(db, actor.user_id)

    keys = [i.idempotency_key for i in items if i.idempotency_key]
    if len(set(keys)) != len(keys):
        raise DomainError("duplicate_key_in_batch", "idempotency_key repetida dentro do mesmo lote.")
    existing: dict[str, str] = {}
    if keys:
        placeholders = ",".join("?" for _ in keys)
        for r in await db.all(
            f"SELECT id, idempotency_key FROM transactions WHERE user_id = ? AND idempotency_key IN ({placeholders})",
            actor.user_id, *keys,
        ):
            existing[r["idempotency_key"]] = r["id"]

    # Valida TUDO antes de escrever; o erro cita a posição no lote.
    created: list[dict] = []
    duplicates: list[dict] = []
    for index, item in enumerate(items):
        try:
            if item.idempotency_key and item.idempotency_key in existing:
                duplicates.append({"index": index, "id": existing[item.idempotency_key]})
                continue
            cents = parse_amount_to_cents(item.amount)
            occurred = parse_date(item.date, today) if item.date else today
            competence = parse_month(item.competence_month, today) if item.competence_month else month_of(occurred)
            cat = resolve_category(categories, item.category, item.kind)
            description = item.description.strip()
            if not description:
                raise DomainError("description_required", "Descrição é obrigatória.")
            created.append({
                "id": new_id("txn"), "kind": item.kind, "amount_cents": cents, "occurred_on": occurred,
                "competence_month": competence, "category_id": cat.id, "category": cat.path,
                "description": description, "note": (item.note or "").strip() or None,
                "idempotency_key": item.idempotency_key,
            })
        except DomainError as e:
            raise DomainError(e.code, f'Item {index + 1} do lote ("{item.description}"): {e.message}', e.details) from e

    stmts = []
    for r in created:
        stmts.append(db.stmt(
            """INSERT INTO transactions (id, user_id, kind, amount_cents, occurred_on, competence_month, category_id,
                 description, note, source, client, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'mcp', ?, ?)""",
            r["id"], actor.user_id, r["kind"], r["amount_cents"], r["occurred_on"], r["competence_month"],
            r["category_id"], r["description"], r["note"], actor.actor, r["idempotency_key"],
        ))
        stmts.append(audit_stmt(db, actor, "transaction.create", r["id"], r))
    try:
        await db.batch(stmts)  # D1 batch = transação
    except Exception as e:
        if "UNIQUE" in str(e):
            raise DomainError(
                "idempotency_conflict",
                "Conflito de idempotency_key (requisição concorrente). Tente novamente: itens já gravados serão reconhecidos.",
            ) from e
        raise

    for r in created:
        r["valor"] = format_brl(r["amount_cents"])
    net = sum(r["amount_cents"] if r["kind"] == "income" else -r["amount_cents"] for r in created)
    return {"created": created, "already_existed": duplicates, "net_cents": net, "saldo_do_lote": format_brl(net)}


async def list_transactions(
    db: DB,
    actor: Actor,
    *,
    month: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    kind: Kind | None = None,
    category: str | None = None,
    search: str | None = None,
    only_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    today = today_in(actor.timezone)
    where = ["t.user_id = ?"]
    args: list[Any] = [actor.user_id]
    where.append("t.deleted_at IS NOT NULL" if only_deleted else "t.deleted_at IS NULL")
    if month:
        where.append("t.competence_month = ?")
        args.append(parse_month(month, today))
    if date_from:
        where.append("t.occurred_on >= ?")
        args.append(parse_date(date_from, today))
    if date_to:
        where.append("t.occurred_on <= ?")
        args.append(parse_date(date_to, today))
    if kind:
        where.append("t.kind = ?")
        args.append(kind)
    if category:
        cat = resolve_category(await load_categories(db, actor.user_id), category, kind)
        where.append("(t.category_id = ? OR c.parent_id = ?)")  # raiz inclui subcategorias
        args += [cat.id, cat.id]
    if search:
        like = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(t.description LIKE ? ESCAPE '\\' OR t.note LIKE ? ESCAPE '\\')")
        args += [like, like]
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    w = " AND ".join(where)
    rows, agg = await db.batch_all([
        db.stmt(f"{_SELECT_VIEW} WHERE {w} ORDER BY t.occurred_on DESC, t.created_at DESC LIMIT ? OFFSET ?", *args, limit, offset),
        db.stmt(
            f"""SELECT t.kind AS kind, COUNT(*) AS n, SUM(t.amount_cents) AS total FROM transactions t
                JOIN categories c ON c.id = t.category_id WHERE {w} GROUP BY t.kind""",
            *args,
        ),
    ])
    by_kind = {a["kind"]: a for a in agg}
    income = int(by_kind.get("income", {}).get("total") or 0)
    expense = int(by_kind.get("expense", {}).get("total") or 0)
    count = sum(int(a["n"]) for a in agg)
    items = [_view(r, today) for r in rows]
    return {
        "items": items,
        "count": count,
        "has_more": offset + len(items) < count,
        "totals": {"income_cents": income, "expense_cents": expense, "entradas": format_brl(income), "saidas": format_brl(expense)},
    }


async def _get_owned(db: DB, actor: Actor, txn_id: str) -> dict:
    row = await db.first(f"{_SELECT_VIEW} WHERE t.user_id = ? AND t.id = ?", actor.user_id, txn_id)
    if not row:
        raise DomainError("not_found", f"Lançamento {txn_id} não encontrado.")
    return row


_UNSET: Any = object()


async def update_transaction(
    db: DB,
    actor: Actor,
    txn_id: str,
    *,
    amount: str | int | float | None = None,
    category: str | None = None,
    description: str | None = None,
    date: str | None = None,
    competence_month: str | None = None,
    note: Any = _UNSET,
) -> dict:
    before = await _get_owned(db, actor, txn_id)
    if before["deleted_at"]:
        raise DomainError("deleted", "Lançamento está excluído; restaure antes de editar.")
    today = today_in(actor.timezone)
    sets: list[str] = []
    args: list[Any] = []
    changes: dict[str, Any] = {}

    def put(col: str, val: Any, label: str | None = None) -> None:
        sets.append(f"{col} = ?")
        args.append(val)
        changes[label or col] = val

    if amount is not None:
        put("amount_cents", parse_amount_to_cents(amount))
    if category is not None:
        cat = resolve_category(await load_categories(db, actor.user_id), category, before["kind"])
        sets.append("category_id = ?")
        args.append(cat.id)
        changes["category"] = cat.path
    if description is not None:
        d = description.strip()
        if not d:
            raise DomainError("description_required", "Descrição não pode ficar vazia.")
        put("description", d)
    if date is not None:
        put("occurred_on", parse_date(date, today))
    if competence_month is not None:
        put("competence_month", parse_month(competence_month, today))
    if note is not _UNSET:
        put("note", (note or "").strip() or None)
    if not sets:
        raise DomainError("nothing_to_update", "Nenhum campo para alterar.")
    if before["recurrence_id"] and set(changes) - {"note"}:
        # Exceção pontual (rateio, valor diferente num mês): o reajuste/encerramento da regra não mexe mais nele.
        sets.append("recurrence_override = 1")

    await db.batch([
        db.stmt(f"UPDATE transactions SET {', '.join(sets)}, updated_at = ? WHERE user_id = ? AND id = ?",
                *args, now_iso(), actor.user_id, txn_id),
        audit_stmt(db, actor, "transaction.update", txn_id, {"before": before, "changes": changes}),
    ])
    return _view(await _get_owned(db, actor, txn_id), today)


async def set_deleted(db: DB, actor: Actor, ids: list[str], deleted: bool) -> dict:
    rows = [await _get_owned(db, actor, i) for i in ids]
    targets = [r for r in rows if (not r["deleted_at"]) == deleted]
    if targets:
        at = now_iso()
        stmts = []
        for r in targets:
            stmts.append(db.stmt("UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                                 at if deleted else None, at, actor.user_id, r["id"]))
            stmts.append(audit_stmt(db, actor, "transaction.delete" if deleted else "transaction.restore", r["id"]))
        await db.batch(stmts)
    target_ids = {r["id"] for r in targets}
    return {
        ("deleted" if deleted else "restored"): [
            {"id": r["id"], "description": r["description"], "valor": format_brl(r["amount_cents"])} for r in targets
        ],
        "unchanged": [r["id"] for r in rows if r["id"] not in target_ids],
    }
