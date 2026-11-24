from ..db import DB
from ..lib.dates import parse_month, today_in
from ..lib.errors import DomainError
from ..lib.ids import new_id
from ..lib.money import format_brl, parse_amount_to_cents
from .categories import load_categories, resolve_category
from .context import Actor, audit_stmt, now_iso


async def set_budget(
    db: DB,
    actor: Actor,
    month: str,
    amount: str | int | float | None = None,
    category: str | None = None,
    remove: bool = False,
) -> dict:
    """Define (upsert) ou remove a meta de gasto do mês. Sem categoria = teto global."""
    m = parse_month(month, today_in(actor.timezone))
    cat = resolve_category(await load_categories(db, actor.user_id), category, "expense") if category else None
    key = cat.id if cat else "*"
    scope = cat.path if cat else "global"

    if remove:
        await db.batch([
            db.stmt("DELETE FROM budgets WHERE user_id = ? AND month = ? AND category_key = ?", actor.user_id, m, key),
            audit_stmt(db, actor, "budget.remove", f"{m}:{key}"),
        ])
        return {"month": m, "scope": scope, "removed": True}
    if amount is None:
        raise DomainError("amount_required", "Informe amount, ou remove=true para apagar a meta.")
    cents = parse_amount_to_cents(amount)
    await db.batch([
        db.stmt(
            """INSERT INTO budgets (id, user_id, month, category_id, category_key, amount_cents) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, month, category_key) DO UPDATE SET amount_cents = excluded.amount_cents, updated_at = ?""",
            new_id("bud"), actor.user_id, m, cat.id if cat else None, key, cents, now_iso(),
        ),
        audit_stmt(db, actor, "budget.set", f"{m}:{key}", {"amount_cents": cents}),
    ])
    return {"month": m, "scope": scope, "amount_cents": cents, "valor": format_brl(cents)}
