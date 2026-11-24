"""Acesso ao D1 a partir do Python Worker.

O SDK `workers` já converte argumentos (None → null) e resultados (JS → dict/list). Este módulo só
padroniza a forma: `all` → list[dict], `first` → dict | None, `run` → nº de linhas alteradas,
`batch` → transação atômica (D1 batch = tudo ou nada).
"""

from typing import Any, Protocol


class Stmt(Protocol):  # D1PreparedStatement embrulhado pelo SDK
    ...


class DB:
    def __init__(self, d1: Any):
        self._d1 = d1

    def stmt(self, sql: str, *args: Any) -> Stmt:
        s = self._d1.prepare(sql)
        return s.bind(*args) if args else s

    async def all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        res = await self.stmt(sql, *args).all()
        return list(_get(res, "results") or [])

    async def first(self, sql: str, *args: Any) -> dict[str, Any] | None:
        row = await self.stmt(sql, *args).first()
        return dict(row) if row else None

    async def run(self, sql: str, *args: Any) -> int:
        res = await self.stmt(sql, *args).run()
        return int(_get(_get(res, "meta") or {}, "changes") or 0)

    async def batch(self, stmts: list[Stmt]) -> list[dict[str, Any]]:
        if not stmts:
            return []
        return list(await self._d1.batch(stmts))

    async def batch_all(self, stmts: list[Stmt]) -> list[list[dict[str, Any]]]:
        """Batch de SELECTs: devolve as linhas de cada um, na ordem."""
        return [list(_get(r, "results") or []) for r in await self.batch(stmts)]


def changes(result: dict[str, Any]) -> int:
    return int(_get(_get(result, "meta") or {}, "changes") or 0)


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
