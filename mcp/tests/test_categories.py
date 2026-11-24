import pytest

from limafinance.domain.categories import CategoryRow, resolve_category
from limafinance.lib.errors import DomainError

ROWS = [
    CategoryRow("cat_alim", None, "Alimentação", "alimentacao", "expense"),
    CategoryRow("cat_merc", "cat_alim", "Mercado", "mercado", "expense"),
    CategoryRow("cat_rest", "cat_alim", "Restaurante", "restaurante", "expense"),
    CategoryRow("cat_casa", None, "Casa", "casa", "expense"),
    CategoryRow("cat_cmerc", "cat_casa", "Mercado", "mercado", "expense"),
    CategoryRow("cat_sal", None, "Salário", "salario", "income"),
    CategoryRow("cat_old", None, "Antiga", "antiga", "expense", archived_at="2024-01-01"),
]


def code(ref, kind=None):
    with pytest.raises(DomainError) as e:
        resolve_category(ROWS, ref, kind)
    return e.value.code


def test_root_ignores_accent_and_case():
    assert resolve_category(ROWS, "alimentacao").id == "cat_alim"
    assert resolve_category(ROWS, "  SALÁRIO ", "income").id == "cat_sal"


def test_path_with_separator():
    assert resolve_category(ROWS, "Alimentação > Mercado").id == "cat_merc"
    assert resolve_category(ROWS, "casa/mercado").id == "cat_cmerc"
    assert resolve_category(ROWS, "Alimentação > Mercado").path == "Alimentação > Mercado"


def test_unique_subcategory_by_name():
    assert resolve_category(ROWS, "restaurante").id == "cat_rest"


def test_strict_not_found_never_creates():
    assert code("Viagem") == "category_not_found"
    assert code("Antiga") == "category_not_found"


def test_suggestions():
    with pytest.raises(DomainError) as e:
        resolve_category(ROWS, "Alimentacão > Mercados")
    assert "Você quis dizer" in e.value.message
    assert "Alimentação > Mercado" in e.value.details["suggestions"]


def test_ambiguous():
    assert code("Mercado") == "ambiguous_category"


def test_kind_mismatch():
    assert code("Salário", "expense") == "category_kind_mismatch"
