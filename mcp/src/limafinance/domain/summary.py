from ..db import DB
from ..lib.dates import days_in_month, month_of, parse_month, today_in
from ..lib.money import format_brl
from .context import Actor


def _line(category_id: str, name: str, spent: int, budget: int | None) -> dict:
    return {
        "category_id": category_id,
        "name": name,
        "spent_cents": spent,
        "budget_cents": budget,
        "remaining_cents": None if budget is None else budget - spent,
        "pct_used": round(spent / budget * 100, 1) if budget else None,
        "gasto": format_brl(spent),
        "meta": None if budget is None else format_brl(budget),
        "restante": None if budget is None else format_brl(budget - spent),
    }


async def month_summary(db: DB, actor: Actor, month: str | None = None) -> dict:
    """Resumo do mês de competência: entradas, saídas por categoria vs. meta, saldo e teto global."""
    today = today_in(actor.timezone)
    m = parse_month(month, today) if month else month_of(today)

    aggs, budgets, pending = await db.batch_all([
        db.stmt(
            """SELECT t.kind, t.category_id, c.name, c.parent_id, p.name AS parent_name, SUM(t.amount_cents) AS total
               FROM transactions t JOIN categories c ON c.id = t.category_id LEFT JOIN categories p ON p.id = c.parent_id
               WHERE t.user_id = ? AND t.competence_month = ? AND t.deleted_at IS NULL
               GROUP BY t.kind, t.category_id""",
            actor.user_id, m,
        ),
        db.stmt(
            """SELECT b.category_id, b.amount_cents, c.name, c.parent_id FROM budgets b
               LEFT JOIN categories c ON c.id = b.category_id WHERE b.user_id = ? AND b.month = ?""",
            actor.user_id, m,
        ),
        db.stmt(
            """SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents), 0) AS total FROM transactions
               WHERE user_id = ? AND competence_month = ? AND deleted_at IS NULL AND kind = 'expense' AND occurred_on > ?""",
            actor.user_id, m, today,
        ),
    ])
    budget_of = {(b["category_id"] or "*"): int(b["amount_cents"]) for b in budgets}
    income = sum(int(a["total"]) for a in aggs if a["kind"] == "income")
    expense = sum(int(a["total"]) for a in aggs if a["kind"] == "expense")

    # Saídas agrupadas por categoria raiz, com quebra por subcategoria.
    roots: dict[str, dict] = {}
    for a in (x for x in aggs if x["kind"] == "expense"):
        root_id = a["parent_id"] or a["category_id"]
        r = roots.setdefault(root_id, {"name": a["parent_name"] or a["name"], "spent": 0, "subs": {}})
        r["spent"] += int(a["total"])
        if a["parent_id"]:
            r["subs"][a["category_id"]] = {"name": a["name"], "spent": int(a["total"])}
    for b in budgets:  # categorias com meta e sem gasto também aparecem
        if not b["category_id"]:
            continue
        root_id = b["parent_id"] or b["category_id"]
        r = roots.setdefault(root_id, {"name": b["name"] if not b["parent_id"] else "(categoria)", "spent": 0, "subs": {}})
        if b["parent_id"]:
            r["subs"].setdefault(b["category_id"], {"name": b["name"], "spent": 0})

    categories = sorted(
        (
            {
                **_line(rid, r["name"], r["spent"], budget_of.get(rid)),
                "subcategories": [_line(sid, s["name"], s["spent"], budget_of.get(sid)) for sid, s in r["subs"].items()],
            }
            for rid, r in roots.items()
        ),
        key=lambda c: -c["spent_cents"],
    )

    global_budget = budget_of.get("*")
    y, mo = map(int, m.split("-"))
    days_left = days_in_month(y, mo) - int(today[8:10]) + 1 if m == month_of(today) else None
    gb = None
    if global_budget is not None:
        left = global_budget - expense
        gb = {
            **_line("*", "Teto global", expense, global_budget),
            "per_day_left": format_brl(left // days_left) if days_left and left > 0 else None,
        }
    p = pending[0] if pending else {"n": 0, "total": 0}
    return {
        "month": m,
        "today": today,
        "income_cents": income,
        "expense_cents": expense,
        "net_cents": income - expense,
        "entradas": format_brl(income),
        "saidas": format_brl(expense),
        "saldo_do_mes": format_brl(income - expense),
        "global_budget": gb,
        "days_left_in_month": days_left,
        "scheduled_future_expenses": {"count": int(p["n"]), "total": format_brl(int(p["total"]))},
        "categories": categories,
    }
