"""Tipos dos parâmetros das tools (Pydantic → JSON Schema que o LLM lê). Descrições em pt-BR, curtas, com exemplo."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

KindT = Annotated[Literal["income", "expense"], Field(description='"expense" = saída/gasto, "income" = entrada/provento')]

AmountT = Annotated[
    str | float,
    Field(description='Valor POSITIVO em reais. Prefira string pt-BR: "45,90", "1.234,56". O sinal vem de kind.'),
]

CategoryT = Annotated[
    str,
    StringConstraints(min_length=1, max_length=120),
    Field(description='Categoria existente: "Alimentação" ou "Alimentação > Mercado". Use list_categories se não souber. Nunca invente.'),
]

DateT = Annotated[
    str,
    StringConstraints(max_length=20),
    Field(description='Data real: "DD/MM/AAAA", "hoje", "ontem". Padrão: hoje (America/Sao_Paulo).'),
]

MonthT = Annotated[
    str,
    StringConstraints(max_length=30),
    Field(description='Mês de competência: "MM/AAAA", "mês passado", "este mês". Padrão: mês da data.'),
]

DescriptionT = Annotated[str, StringConstraints(min_length=1, max_length=200), Field(description='Descrição curta: "Mercado Pão de Açúcar"')]
NoteT = Annotated[str, StringConstraints(max_length=500), Field(description="Nota livre opcional")]

TxnIdT = Annotated[str, StringConstraints(pattern=r"^txn_[0-9a-z]{22}$"), Field(description="id do lançamento (txn_...)")]
RecIdT = Annotated[str, StringConstraints(pattern=r"^rec_[0-9a-z]{22}$"), Field(description="id da recorrência (rec_...)")]


class NewTransactionInput(BaseModel):
    kind: KindT
    amount: AmountT
    category: CategoryT
    description: DescriptionT
    date: DateT | None = None
    competence_month: MonthT | None = None
    note: NoteT | None = None
    idempotency_key: Annotated[
        str,
        StringConstraints(min_length=8, max_length=100),
        Field(description="Chave única por lançamento (ex.: UUID). Reenviar a mesma chave NÃO duplica. Gere uma nova por item."),
    ] | None = None
