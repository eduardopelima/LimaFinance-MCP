"""Recorrências com horizonte de 12 meses, reajuste, exceção pontual e encerramento (SQLite real com as migrations)."""

import asyncio

import pytest
from fakes import FakeD1

from limafinance.auth import upsert_user
from limafinance.db import DB
from limafinance.domain import categories, recurrences, summary, transactions
from limafinance.domain.context import Actor
from limafinance.lib.dates import add_months, month_of, today_in


def run(coro):
    return asyncio.run(coro)


CUR = month_of(today_in())


def m(n: int) -> str:
    return add_months(CUR, n)


@pytest.fixture
def ctx():
    db = DB(FakeD1())
    u = run(upsert_user(db, "ana@example.com"))
    a = Actor(user_id=u["id"], email=u["email"], timezone="America/Sao_Paulo", actor="test", scopes=("mcp:read", "mcp:write"))
    run(categories.create_category(db, a, "Moradia", None, "expense"))
    run(categories.create_category(db, a, "Lazer", None, "expense"))
    return db, a


def generated(db, a, rec_id, *, deleted=False):
    return run(db.all(
        f"""SELECT competence_month AS m, amount_cents AS c, occurred_on AS d, description, recurrence_override AS o
            FROM transactions WHERE user_id = ? AND recurrence_id = ? AND deleted_at IS {'NOT ' if deleted else ''}NULL
            ORDER BY competence_month""",
        a.user_id, rec_id,
    ))


def create(db, a, **kw):
    base = dict(kind="expense", amount="400", category="Moradia", description="Condomínio", day_of_month=10)
    return run(recurrences.create_recurrence(db, a, **{**base, **kw}))


def test_create_without_end_generates_12_months_now(ctx):
    db, a = ctx
    r = create(db, a)
    rows = generated(db, a, r["id"])
    assert [x["m"] for x in rows] == [m(i) for i in range(12)]
    assert all(x["c"] == 40000 and x["d"] == f"{x['m']}-10" for x in rows)
    assert r["generated_count"] == 12


def test_materialize_is_idempotent_and_rolls_the_window(ctx):
    db, a = ctx
    r = create(db, a)
    assert run(recurrences.materialize(db, a.user_id, CUR)) == 0  # cron no mesmo mês: nada novo
    assert run(recurrences.materialize(db, a.user_id, m(1))) == 1  # virada do mês: entra m(12)
    assert generated(db, a, r["id"])[-1]["m"] == m(12)


def test_future_start_end_month_and_day_clamp(ctx):
    db, a = ctx
    r = create(db, a, start_month=m(2), end_month=m(4), day_of_month=31)
    rows = generated(db, a, r["id"])
    assert [x["m"] for x in rows] == [m(2), m(3), m(4)]
    assert all(x["d"][8:] in ("28", "29", "30", "31") for x in rows)


def test_past_start_needs_include_past_months(ctx):
    db, a = ctx
    r1 = create(db, a, start_month=m(-2))
    assert generated(db, a, r1["id"])[0]["m"] == CUR and "note" in r1
    r2 = create(db, a, start_month=m(-2), description="Aluguel garagem", include_past_months=True)
    assert generated(db, a, r2["id"])[0]["m"] == m(-2)


def test_one_off_exception_survives_regeneration_and_adjustment(ctx):
    db, a = ctx
    r = create(db, a)
    rows = run(db.all("SELECT id, competence_month FROM transactions WHERE recurrence_id = ? ORDER BY competence_month", r["id"]))
    rateio = [x["id"] for x in rows[2:5]]  # 3 meses de rateio: 400 → 1000
    for tid in rateio:
        run(transactions.update_transaction(db, a, tid, amount="1000"))
    run(recurrences.materialize(db, a.user_id, CUR))  # não sobrescreve

    res = run(recurrences.update_recurrence(db, a, r["id"], from_month=m(1), amount="450"))
    got = {x["m"]: x["c"] for x in generated(db, a, r["id"])}
    assert got[CUR] == 40000  # antes do reajuste
    assert got[m(1)] == 45000 and got[m(5)] == 45000 and got[m(11)] == 45000
    assert got[m(2)] == got[m(3)] == got[m(4)] == 100000  # rateio intocado
    assert [k["month"] for k in res["kept_edited"]] == [m(2), m(3), m(4)]
    assert res["updated_transactions"] == 8  # m(1)..m(11) menos os 3 do rateio
    run(recurrences.materialize(db, a.user_id, m(1)))  # mês novo da janela nasce com o valor reajustado
    assert generated(db, a, r["id"])[-1]["c"] == 45000


def test_retroactive_adjustment_and_note_edit_does_not_detach(ctx):
    db, a = ctx
    r = create(db, a, start_month=m(-2), include_past_months=True)
    first = run(db.first("SELECT id FROM transactions WHERE recurrence_id = ? AND competence_month = ?", r["id"], m(-2)))
    run(transactions.update_transaction(db, a, first["id"], note="pago no pix"))
    run(recurrences.update_recurrence(db, a, r["id"], from_month=m(-2), amount="420", day_of_month=5))
    rows = generated(db, a, r["id"])
    assert all(x["c"] == 42000 and x["d"] == f"{x['m']}-05" and x["o"] == 0 for x in rows)


def test_adjustment_beyond_horizon_is_rejected(ctx):
    db, a = ctx
    r = create(db, a)
    with pytest.raises(Exception, match="12 meses"):
        run(recurrences.update_recurrence(db, a, r["id"], from_month=m(12), amount="500"))


def test_end_recurrence_removes_future_but_keeps_edited(ctx):
    db, a = ctx
    r = create(db, a)
    edited = run(db.first("SELECT id FROM transactions WHERE recurrence_id = ? AND competence_month = ?", r["id"], m(6)))
    run(transactions.update_transaction(db, a, edited["id"], amount="999"))
    res = run(recurrences.end_recurrence(db, a, r["id"], m(2)))
    assert [x["m"] for x in generated(db, a, r["id"])] == [CUR, m(1), m(2), m(6)]
    assert res["removed_future_transactions"] == 8 and res["kept_edited"][0]["month"] == m(6)
    assert run(recurrences.materialize(db, a.user_id, m(1))) == 0  # regra encerrada não gera mais


def test_deleted_month_is_not_recreated(ctx):
    db, a = ctx
    r = create(db, a)
    t = run(db.first("SELECT id FROM transactions WHERE recurrence_id = ? AND competence_month = ?", r["id"], m(3)))
    run(transactions.set_deleted(db, a, [t["id"]], True))
    run(recurrences.materialize(db, a.user_id, CUR))
    assert m(3) not in [x["m"] for x in generated(db, a, r["id"])]


def test_summary_and_list_split_forecast(ctx):
    db, a = ctx
    create(db, a, start_month=m(1))
    s = run(summary.month_summary(db, a, m(1)))
    assert s["expense_cents"] == 40000 and s["previsto"]["expense_cents"] == 40000 and s["realizado"]["expense_cents"] == 0
    lt = run(transactions.list_transactions(db, a, month=m(1)))
    assert lt["items"][0]["previsto"] is True
