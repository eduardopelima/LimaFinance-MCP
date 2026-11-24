"""D1 falso sobre SQLite em memória, com as migrations reais. Imita o formato que o SDK `workers` devolve
(`all` → {"results", "meta"}, `first` → dict | None, `run` → {"meta": {"changes"}}, `batch` → transação)."""

import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


class _Stmt:
    def __init__(self, conn: sqlite3.Connection, sql: str, args: tuple = ()):
        self._conn, self._sql, self._args = conn, sql, args

    def bind(self, *args):
        return _Stmt(self._conn, self._sql, args)

    def _exec(self) -> dict:
        cur = self._conn.execute(self._sql, self._args)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        return {"results": rows, "meta": {"changes": cur.rowcount if cur.rowcount > 0 else 0}}

    async def all(self) -> dict:
        res = self._exec()
        self._conn.commit()
        return res

    async def first(self):
        res = self._exec()
        self._conn.commit()
        return res["results"][0] if res["results"] else None

    async def run(self) -> dict:
        res = self._exec()
        self._conn.commit()
        return res


class FakeD1:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        for f in sorted(MIGRATIONS.glob("*.sql")):
            self.conn.executescript(f.read_text(encoding="utf-8"))

    def prepare(self, sql: str) -> _Stmt:
        return _Stmt(self.conn, sql)

    async def batch(self, stmts: list[_Stmt]) -> list[dict]:
        out = []
        try:
            for s in stmts:
                out.append(s._exec())
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return out
