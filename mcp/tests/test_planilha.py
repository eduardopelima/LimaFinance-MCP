from planilha_parser import parse_csv, parse_planilha

CSV = """Mês-Competência;Data;Categoria;Subcategoria;Descrição;Ocorrência;Valor;Provento;Meta Gasto;Nota
dez/23;05/12/2023;Alimentação;Mercado;Pão de Açúcar;Pontual;R$ 345,90;R$ 8.500,00;R$ 6.000,00;
dez/23;10/12/2023;Moradia;;Aluguel;Mensal;R$ 2.500,00;R$ 8.500,00;R$ 6.000,00;"nota; com ponto e vírgula"
dez/23;10/12/2023;Moradia;;Aluguel;Mensal;R$ 2.500,00;;;
01/01/2024;;Salário;;Bônus;Pontual;1.000,00;;;
jan/24;03/01/2024;Alimentação;Restaurante;Almoço;Pontual;-50,00;R$ 9.000,00;;
"""

TODAY = "2025-01-15"


def parsed():
    return parse_planilha(CSV, today=TODAY, income_categories=["Salário"])


def test_csv_quotes_and_delimiter():
    rows = parse_csv(CSV)
    assert rows[2][9] == "nota; com ponto e vírgula"
    assert len(rows) == 6


def test_no_errors():
    assert parsed().errors == []


def test_values_dates_competence():
    t = parsed().transactions[0]
    assert (t.kind, t.amount_cents, t.occurred_on, t.competence_month, t.category, t.subcategory) == (
        "expense", 34590, "2023-12-05", "2023-12", "Alimentação", "Mercado",
    )


def test_competence_as_full_date_and_empty_date():
    bonus = next(t for t in parsed().transactions if t.description == "Bônus")
    assert (bonus.kind, bonus.competence_month, bonus.occurred_on, bonus.amount_cents) == ("income", "2024-01", "2024-01-01", 100000)


def test_identical_rows_get_distinct_deterministic_keys():
    r = parsed()
    aluguel = [t for t in r.transactions if t.description == "Aluguel"]
    assert len(aluguel) == 2 and aluguel[0].idempotency_key != aluguel[1].idempotency_key
    assert all(a.monthly for a in aluguel)
    assert [t.idempotency_key for t in parsed().transactions] == [t.idempotency_key for t in r.transactions]


def test_month_level_income_and_budget():
    r = parsed()
    assert r.income_by_month == {"2023-12": 850000, "2024-01": 900000}
    assert r.budget_by_month == {"2023-12": 600000}


def test_negative_warning():
    assert any("-50,00" in w for w in parsed().warnings)


def test_conflicting_provento():
    bad = parse_planilha("Mês-Competência;Categoria;Descrição;Valor;Provento\ndez/23;A;x;1,00;100\ndez/23;A;y;1,00;200\n", today=TODAY)
    assert "Provento de 2023-12" in bad.errors[0]


def test_text_in_month_level_column_is_warning_not_crash():
    r = parse_planilha("Mês-Competência;Categoria;Descrição;Valor;Provento\ndez/23;A;x;1,00;abc\n", today=TODAY)
    assert r.errors == [] and r.income_by_month == {}
    assert any("Linha 2" in w and "abc" in w for w in r.warnings)
    assert r.transactions[0].note == "Provento: abc"


def test_missing_required_columns():
    bad = parse_planilha("Data;Valor\n01/01/2024;10\n", today=TODAY)
    assert "Colunas obrigatórias ausentes" in bad.errors[0]


def test_zero_values_skipped_and_text_in_value_column_becomes_note():
    csv_ = (
        "Mês-Competência;Categoria;Descrição;Valor;Meta Gasto\n"
        "dez/23;Conta;Academia;R$ 0,00;\n"
        "dez/23;Mercado;Mercado do mês;R$ 1.300,00;---A FECHAR\n"
        "dez/23;;temaki;50;\n"
    )
    r = parse_planilha(csv_, today=TODAY)
    assert r.skipped_zero == 1
    merc = next(t for t in r.transactions if t.description == "Mercado do mês")
    assert merc.note == "Meta Gasto: ---A FECHAR" and r.budget_by_month == {}
    assert any("Categoria vazia" in e for e in r.errors)  # sem default: erro, nunca inventa categoria
    r2 = parse_planilha(csv_, today=TODAY, default_category="A classificar")
    assert r2.errors == [] and next(t for t in r2.transactions if t.description == "temaki").category == "A classificar"


def test_until_defers_future_months_and_skip_uncategorized():
    csv_ = (
        "Mês-Competência;Data;Categoria;Descrição;Ocorrência;Valor;Provento;Meta Gasto\n"
        "11/2026;05/11/2026;Conta;Luz;Mensal;100,00;5.000,00;3.000,00\n"
        "12/2026;;Conta;Luz;Mensal;110,00;4.000,00;2.000,00\n"
        "11/2026;;;temaki;;50;;\n"
    )
    r = parse_planilha(csv_, today="2026-11-24", until="2026-11", skip_uncategorized=True)
    assert r.errors == [] and r.skipped_uncategorized == 1
    assert [t.competence_month for t in r.transactions] == ["2026-11"]
    assert [(t.competence_month, t.amount_cents, t.monthly) for t in r.deferred] == [("2026-12", 11000, True)]
    assert r.income_by_month == {"2026-11": 500000} and r.budget_by_month == {"2026-11": 300000}
