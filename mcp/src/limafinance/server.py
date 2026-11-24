"""Fábrica do servidor MCP (stateless: uma instância por requisição) e registro das 13 tools."""

import functools
import json
import traceback
from typing import Annotated, Any, Awaitable, Callable
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, StringConstraints

from .auth import LimaTokenVerifier, current_actor
from .db import DB
from .domain import budgets, categories, recurrences, summary, transactions
from .domain.context import SCOPE_READ, SCOPE_WRITE, Actor
from .lib.dates import month_of, today_in
from .lib.errors import DomainError
from .schemas import (
    AmountT, CategoryT, DateT, DescriptionT, KindT, MonthT, NewTransactionInput, NoteT, RecIdT, TxnIdT,
)

INSTRUCTIONS = """Controle financeiro pessoal do usuário (BRL, pt-BR).
- Comece com get_context para saber a data de hoje (America/Sao_Paulo) e as categorias.
- Valores: sempre positivos; use kind = "expense" (saída) ou "income" (entrada).
- Competência (mês a que o lançamento pertence) é diferente da data real; pergunte se for ambíguo (ex.: fatura de cartão).
- Nunca invente categoria: se não existir, mostre as sugestões e pergunte ao usuário.
- Confirme com o usuário antes de excluir. Use idempotency_key em todo lançamento."""

READ = {"readOnlyHint": True, "openWorldHint": False}


def _write(idempotent: bool, destructive: bool = False) -> dict:
    return {"readOnlyHint": False, "destructiveHint": destructive, "idempotentHint": idempotent, "openWorldHint": False}


def guarded(write: bool = False):
    """Converte DomainError em resultado `isError` (JSON legível p/ o LLM) e checa o escopo de escrita."""

    def deco(fn: Callable[..., Awaitable[Any]]):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                actor = current_actor()
                if write and SCOPE_WRITE not in actor.scopes:
                    raise DomainError("forbidden", "Este token só tem permissão de leitura (falta o escopo mcp:write).")
                return await fn(*args, **kwargs)
            except DomainError as e:
                raise ToolError(json.dumps({"error": e.code, "message": e.message, **e.details}, ensure_ascii=False)) from e
            except ToolError:
                raise
            except Exception:
                traceback.print_exc()
                raise ToolError("Erro interno no servidor. Tente novamente.") from None

        return wrapper

    return deco


def build_server(env: Any) -> MCPServer:
    resource = env.MCP_RESOURCE
    db = DB(env.DB)
    mcp = MCPServer(
        "limafinance",
        version="0.2.0",
        instructions=INSTRUCTIONS,
        token_verifier=LimaTokenVerifier(env, resource),
        auth=AuthSettings(
            issuer_url=env.AUTH_ISSUER,
            resource_server_url=resource,
            required_scopes=[SCOPE_READ],
            validate_token_resource=True,
        ),
    )

    def actor() -> Actor:
        return current_actor()

    # ---------------- leitura ----------------

    @mcp.tool(annotations=READ)
    @guarded()
    async def get_context() -> dict:
        """Chame primeiro numa conversa nova. Retorna a data de HOJE no fuso do usuário, o mês de competência atual e a árvore de categorias."""
        a = actor()
        today = today_in(a.timezone)
        return {
            "user": a.email, "timezone": a.timezone, "today": today, "current_month": month_of(today), "currency": "BRL",
            "categories": categories.category_tree(await categories.load_categories(db, a.user_id)),
        }

    @mcp.tool(annotations=READ)
    @guarded()
    async def list_categories(kind: KindT | None = None) -> list[dict]:
        """Árvore de categorias e subcategorias do usuário, por tipo (income/expense)."""
        tree = categories.category_tree(await categories.load_categories(db, actor().user_id))
        return [c for c in tree if not kind or c["kind"] == kind]

    @mcp.tool(annotations=READ)
    @guarded()
    async def list_transactions(
        month: MonthT | None = None,
        date_from: DateT | None = None,
        date_to: DateT | None = None,
        kind: KindT | None = None,
        category: CategoryT | None = None,
        search: Annotated[str, StringConstraints(max_length=100), Field(description="Texto na descrição ou nota")] | None = None,
        only_deleted: Annotated[bool, Field(description="Só os excluídos (para restaurar)")] = False,
        limit: Annotated[int, Field(ge=1, le=200, description="Padrão 50")] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict:
        """Lista lançamentos com filtros. `month` filtra por COMPETÊNCIA; `date_from`/`date_to` pela data real. Categoria raiz inclui subcategorias. Retorna totais do filtro."""
        return await transactions.list_transactions(
            db, actor(), month=month, date_from=date_from, date_to=date_to, kind=kind, category=category,
            search=search, only_deleted=only_deleted, limit=limit, offset=offset,
        )

    @mcp.tool(annotations=READ)
    @guarded()
    async def month_summary(month: MonthT | None = None) -> dict:
        """Resumo do mês de competência: entradas, saídas, saldo, gasto por categoria vs. meta e quanto resta do teto global (com R$/dia restante no mês corrente)."""
        return await summary.month_summary(db, actor(), month)

    @mcp.tool(annotations=READ)
    @guarded()
    async def list_recurrences(include_ended: bool = False) -> list[dict]:
        """Regras de lançamentos mensais (assinaturas, aluguel, salário). Por padrão só as ativas."""
        return await recurrences.list_recurrences(db, actor(), include_ended)

    # ---------------- escrita ----------------

    @mcp.tool(annotations=_write(idempotent=False))
    @guarded(write=True)
    async def add_transactions(items: Annotated[list[NewTransactionInput], Field(min_length=1, max_length=100)]) -> dict:
        """Registra 1 a 100 entradas/saídas num lote ATÔMICO (se um item falhar, nada é gravado). Categoria precisa existir: se não existir, retorna erro com sugestões; pergunte ao usuário. Envie idempotency_key em cada item para que retentativas não dupliquem."""
        return await transactions.add_transactions(
            db, actor(), [transactions.NewTransaction(**i.model_dump()) for i in items]
        )

    @mcp.tool(annotations=_write(idempotent=True))
    @guarded(write=True)
    async def update_transaction(
        id: TxnIdT,
        amount: AmountT | None = None,
        category: CategoryT | None = None,
        description: DescriptionT | None = None,
        date: DateT | None = None,
        competence_month: MonthT | None = None,
        note: Annotated[str | None, Field(description='Nova nota; "" apaga a nota. Omitir = não mexe')] = None,
    ) -> dict:
        """Altera só os campos informados de um lançamento. Para trocar kind, exclua e crie outro."""
        kwargs: dict[str, Any] = {}
        if note is not None:
            kwargs["note"] = note
        return await transactions.update_transaction(
            db, actor(), id, amount=amount, category=category, description=description,
            date=date, competence_month=competence_month, **kwargs,
        )

    @mcp.tool(annotations=_write(idempotent=True, destructive=True))
    @guarded(write=True)
    async def delete_transactions(ids: Annotated[list[TxnIdT], Field(min_length=1, max_length=50)]) -> dict:
        """Exclusão LÓGICA (soft delete) de 1 a 50 lançamentos; reversível com restore_transactions. Confirme com o usuário antes."""
        return await transactions.set_deleted(db, actor(), ids, True)

    @mcp.tool(annotations=_write(idempotent=True))
    @guarded(write=True)
    async def restore_transactions(ids: Annotated[list[TxnIdT], Field(min_length=1, max_length=50)]) -> dict:
        """Desfaz a exclusão lógica. Use list_transactions com only_deleted=true para achar os ids."""
        return await transactions.set_deleted(db, actor(), ids, False)

    @mcp.tool(annotations=_write(idempotent=False))
    @guarded(write=True)
    async def create_category(
        name: Annotated[str, StringConstraints(min_length=1, max_length=60)],
        parent: Annotated[str | None, Field(description="Categoria-mãe, para criar subcategoria")] = None,
        kind: KindT | None = None,
    ) -> dict:
        """Cria categoria raiz (informe kind) ou subcategoria (informe parent; herda o kind). Só use quando o usuário pedir explicitamente."""
        return await categories.create_category(db, actor(), name, parent, kind)

    @mcp.tool(annotations=_write(idempotent=True))
    @guarded(write=True)
    async def set_budget(
        month: MonthT,
        amount: AmountT | None = None,
        category: CategoryT | None = None,
        remove: bool = False,
    ) -> dict:
        """Define (ou substitui) a meta de gasto do mês. Sem category = teto global do mês. remove=true apaga a meta."""
        return await budgets.set_budget(db, actor(), month, amount, category, remove)

    @mcp.tool(annotations=_write(idempotent=False))
    @guarded(write=True)
    async def create_recurrence(
        kind: KindT,
        amount: AmountT,
        category: CategoryT,
        description: DescriptionT,
        day_of_month: Annotated[int, Field(ge=1, le=31, description="Dia do mês; 31 = último dia")],
        start_month: Annotated[MonthT | None, Field(description="Padrão: mês corrente")] = None,
        end_month: Annotated[MonthT | None, Field(description="Último mês (inclusive). Omitir = sem fim")] = None,
        note: NoteT | None = None,
    ) -> dict:
        """Cria regra mensal (aluguel, assinatura, salário). O lançamento do mês corrente é gerado na hora; os próximos, automaticamente no início de cada mês. Meses passados NÃO são gerados."""
        return await recurrences.create_recurrence(
            db, actor(), kind=kind, amount=amount, category=category, description=description,
            day_of_month=day_of_month, start_month=start_month, end_month=end_month, note=note,
        )

    @mcp.tool(annotations=_write(idempotent=True))
    @guarded(write=True)
    async def end_recurrence(
        id: RecIdT,
        last_month: Annotated[MonthT | None, Field(description="Último mês em que vale. Padrão: mês corrente")] = None,
    ) -> dict:
        """Encerra uma regra mensal. Lançamentos já gerados permanecem (exclua-os à parte se preciso)."""
        return await recurrences.end_recurrence(db, actor(), id, last_month)

    return mcp


def http_app(env: Any):
    """App ASGI (Starlette) stateless, com Host/Origin restritos ao host público de MCP_RESOURCE."""
    host = urlparse(env.MCP_RESOURCE).netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "localhost:*", "127.0.0.1:*"],
        # Clientes remotos (Claude/ChatGPT) não mandam Origin; navegadores locais (Inspector) mandam localhost.
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*", f"https://{host}"],
    )
    return build_server(env).streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security, host=host.split(":")[0]
    )
