"""Importação ÚNICA do histórico da planilha (não é tool MCP, de propósito).

    uv run scripts/import_planilha.py --file data/planilha.csv --email voce@exemplo.com \\
        [--income-categories "Salário,Rendimentos"] [--provento-category Proventos] [--apply] [--env local|staging|production]

Sem --apply: só valida e gera import-out/import.sql + relatório (dry-run).
Reexecutar é seguro: chaves de idempotência determinísticas; categorias e metas fazem upsert.
"""

import argparse
import hashlib
import sys

sys.stdout.reconfigure(encoding="utf-8")  # console do Windows (cp1252)
import json
from pathlib import Path

from d1 import MCP_DIR, d1_execute, random_id, sql
from planilha_parser import parse_planilha

from limafinance.lib.dates import today_in
from limafinance.lib.money import format_brl
from limafinance.lib.text import normalize_key

p = argparse.ArgumentParser()
p.add_argument("--file", required=True)
p.add_argument("--email", required=True)
p.add_argument("--income-categories", default="")
p.add_argument("--provento-category", default="Proventos")
p.add_argument("--default-category", help="categoria para linhas sem categoria (criada se não existir)")
p.add_argument("--skip-uncategorized", action="store_true", help="ignora (com aviso) linhas sem categoria")
p.add_argument("--until", help="último mês de competência importado (YYYY-MM); meses depois são adiados")
p.add_argument("--recurrences-from-deferred", action="store_true",
               help="linhas Mensal adiadas por --until viram regras de recorrência a partir do mês delas")
p.add_argument("--apply", action="store_true")
p.add_argument("--env", default="local", choices=["local", "staging", "production"])
a = p.parse_args()

email = a.email.strip().lower()
income_categories = [s.strip() for s in a.income_categories.split(",") if s.strip()]
r = parse_planilha(Path(a.file).read_text(encoding="utf-8-sig"), today=today_in(), income_categories=income_categories,
                   default_category=a.default_category, skip_uncategorized=a.skip_uncategorized, until=a.until)

print(f"Linhas de lançamento: {len(r.transactions)}")
print(f"Meses com Provento: {len(r.income_by_month)} | com Meta Gasto: {len(r.budget_by_month)} | valor zero ignorado: {r.skipped_zero}"
      f" | sem categoria ignorado: {r.skipped_uncategorized} | adiados (> {a.until or '-'}): {len(r.deferred)}")
for w in r.warnings:
    print("AVISO", w)
if r.errors:
    for e in r.errors:
        print("ERRO", e)
    raise SystemExit(f"\n{len(r.errors)} erro(s). Nada foi gerado. Corrija o CSV e rode de novo.")

U = f"(SELECT id FROM users WHERE email = {sql(email)})"


def root_id(name: str, kind: str) -> str:
    return f"(SELECT id FROM categories WHERE user_id = {U} AND parent_key = '' AND name_key = {sql(normalize_key(name))} AND kind = {sql(kind)})"


def sub_id(root: str, sub: str, kind: str) -> str:
    return f"(SELECT id FROM categories WHERE user_id = {U} AND parent_key = {root_id(root, kind)} AND name_key = {sql(normalize_key(sub))})"


lines = [f"INSERT INTO users (id, email) VALUES ({sql(random_id('usr'))}, {sql(email)}) ON CONFLICT (email) DO NOTHING;"]

roots: dict[str, tuple[str, str]] = {}
subs: dict[str, tuple[str, str, str]] = {}
rec_rows = [t for t in r.deferred if t.monthly] if a.recurrences_from_deferred else []
for t in [*r.transactions, *rec_rows]:
    roots[f"{t.kind}|{normalize_key(t.category)}"] = (t.category.strip(), t.kind)
    if t.subcategory:
        subs[f"{t.kind}|{normalize_key(t.category)}|{normalize_key(t.subcategory)}"] = (t.category, t.subcategory.strip(), t.kind)
if r.income_by_month:
    roots[f"income|{normalize_key(a.provento_category)}"] = (a.provento_category, "income")

for name, kind in roots.values():
    lines.append(f"""INSERT INTO categories (id, user_id, parent_id, parent_key, name, name_key, kind)
SELECT {sql(random_id('cat'))}, {U}, NULL, '', {sql(name)}, {sql(normalize_key(name))}, {sql(kind)} WHERE true
ON CONFLICT (user_id, parent_key, name_key) DO NOTHING;""")
for root, name, kind in subs.values():
    lines.append(f"""INSERT INTO categories (id, user_id, parent_id, parent_key, name, name_key, kind)
SELECT {sql(random_id('cat'))}, {U}, {root_id(root, kind)}, {root_id(root, kind)}, {sql(name)}, {sql(normalize_key(name))}, {sql(kind)} WHERE true
ON CONFLICT (user_id, parent_key, name_key) DO NOTHING;""")

for t in r.transactions:
    cat = sub_id(t.category, t.subcategory, t.kind) if t.subcategory else root_id(t.category, t.kind)
    lines.append(f"""INSERT INTO transactions (id, user_id, kind, amount_cents, occurred_on, competence_month, category_id, description, note, source, client, idempotency_key)
SELECT {sql(random_id('txn'))}, {U}, {sql(t.kind)}, {t.amount_cents}, {sql(t.occurred_on)}, {sql(t.competence_month)}, {cat}, {sql(t.description)}, {sql(t.note)}, 'import', 'import', {sql(t.idempotency_key)} WHERE true
ON CONFLICT (user_id, idempotency_key) DO NOTHING;""")
for month, cents in r.income_by_month.items():
    lines.append(f"""INSERT INTO transactions (id, user_id, kind, amount_cents, occurred_on, competence_month, category_id, description, source, client, idempotency_key)
SELECT {sql(random_id('txn'))}, {U}, 'income', {cents}, {sql(month + '-01')}, {sql(month)}, {root_id(a.provento_category, 'income')}, 'Provento do mês', 'import', 'import', {sql('imp:provento:' + month)} WHERE true
ON CONFLICT (user_id, idempotency_key) DO NOTHING;""")
for month, cents in r.budget_by_month.items():
    lines.append(f"""INSERT INTO budgets (id, user_id, month, category_id, category_key, amount_cents)
SELECT {sql(random_id('bud'))}, {U}, {sql(month)}, NULL, '*', {cents} WHERE true
ON CONFLICT (user_id, month, category_key) DO UPDATE SET amount_cents = excluded.amount_cents;""")
_B32 = "0123456789abcdefghjkmnpqrstvwxyz"
rec_seen: dict[str, int] = {}
for t in rec_rows:
    # ID determinístico (mesmo formato rec_ + 22 chars): reimportar não duplica a regra.
    base = "|".join([email, normalize_key(t.category), normalize_key(t.subcategory or ""), normalize_key(t.description)])
    rec_seen[base] = rec_seen.get(base, 0) + 1
    digest = hashlib.sha256(f"{base}|{rec_seen[base]}".encode()).digest()
    rid = "rec_" + "".join(_B32[b % 32] for b in digest[:22])
    cat = sub_id(t.category, t.subcategory, t.kind) if t.subcategory else root_id(t.category, t.kind)
    lines.append(f"""INSERT INTO recurrences (id, user_id, kind, amount_cents, category_id, description, note, day_of_month, start_month, end_month)
SELECT {sql(rid)}, {U}, {sql(t.kind)}, {t.amount_cents}, {cat}, {sql(t.description)}, {sql(t.note)}, {int(t.occurred_on[8:10])}, {sql(t.competence_month)}, NULL WHERE true
ON CONFLICT (id) DO NOTHING;""")
lines.append(f"INSERT INTO audit_log (user_id, actor, action, entity_id, payload) SELECT {U}, 'import', 'import.run', {sql(a.file)}, {sql(json.dumps({'rows': len(r.transactions)}))};")

out_dir = MCP_DIR / "import-out"
out_dir.mkdir(exist_ok=True)
sql_path = out_dir / "import.sql"
sql_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------- relatório ----------
by_month: dict[str, list[int]] = {}
for t in r.transactions:
    m = by_month.setdefault(t.competence_month, [0, 0, 0])  # saídas, entradas, n
    m[0 if t.kind == "expense" else 1] += t.amount_cents
    m[2] += 1
report = ["Resumo por competência:"]
for month in sorted(by_month):
    exp, inc, n = by_month[month]
    inc += r.income_by_month.get(month, 0)
    meta = format_brl(r.budget_by_month[month]) if month in r.budget_by_month else "-"
    report.append(f"{month}  {n:4d} lanç.  saídas {format_brl(exp):>14}  entradas {format_brl(inc):>14}  meta {meta}")
monthly: dict[str, tuple[str, str, str, int]] = {}
for t in (x for x in r.transactions if x.monthly):
    k = f"{normalize_key(t.category)}|{normalize_key(t.description)}"
    if k not in monthly or monthly[k][2] < t.competence_month:
        monthly[k] = (" > ".join(filter(None, [t.category, t.subcategory])), t.description, t.competence_month, t.amount_cents)
report += ["", "Candidatas a recorrência (Ocorrência=Mensal) — crie via create_recurrence com start_month APÓS o último mês importado:"]
report += [f"  - {cat}: {desc} ({format_brl(c)}, último: {last})" for cat, desc, last, c in monthly.values()]
if r.deferred:
    report += ["", f"Adiados (competência > {a.until}) — NÃO gravados como lançamentos:"]
    report += [f"  - {t.competence_month} {' > '.join(filter(None, [t.category, t.subcategory]))}: {t.description} ({format_brl(t.amount_cents)})"
               f"{' [Mensal → recorrência]' if t in rec_rows else ''}" for t in r.deferred]
if rec_rows:
    report += ["", f"Recorrências criadas: {len(rec_rows)} (dia do mês = dia da data da linha, ou 1). Gerenciáveis por list_recurrences/end_recurrence."]
(out_dir / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
print("\n" + "\n".join(report))
print(f"\nSQL gerado: {sql_path} ({len(lines)} statements).")

if a.apply:
    print(f"Aplicando no D1 ({a.env})...")
    d1_execute(a.env, file=str(sql_path))
    print("Importação aplicada.")
else:
    print("Dry-run. Para gravar: adicione --apply (e --env production para produção).")
