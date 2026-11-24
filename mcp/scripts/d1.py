"""Utilitários dos scripts (rodam em CPython, fora do Worker)."""

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MCP_DIR / "src"))  # scripts usam limafinance.lib (mesmo código do Worker)
# Estado local compartilhado entre auth/ e mcp/ (o e2e do CI usa outro diretório via LF_PERSIST).
PERSIST = os.environ.get("LF_PERSIST") or str(MCP_DIR.parent / ".wrangler" / "state")
ENVS = ("local", "staging", "production")
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def sql(v) -> str:
    """Literal SQL para valores gerados pelo próprio script (aspas simples duplicadas)."""
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def random_id(prefix: str) -> str:
    t = int(time.time() * 1000)
    head = ""
    for _ in range(10):
        head = _ALPHABET[t % 32] + head
        t //= 32
    return f"{prefix}_{head}" + "".join(_ALPHABET[b % 32] for b in secrets.token_bytes(12))


def d1_execute(env: str, *, command: str | None = None, file: str | None = None) -> str:
    """Executa SQL no D1 via wrangler. env = "local" | "staging" | "production". SQL sempre via arquivo (sem quoting)."""
    if env not in ENVS:
        raise SystemExit(f"--env inválido: {env} (use {', '.join(ENVS)})")
    tmp = None
    if command is not None:
        fd, tmp = tempfile.mkstemp(suffix=".sql")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(command)
        file = tmp
    npx = shutil.which("npx") or "npx"
    args = [npx, "--yes", "wrangler", "d1", "execute", "DB", "--yes", "--file", file]
    args += ["--local", "--persist-to", PERSIST] if env == "local" else ["--remote", "--env", env]
    try:
        res = subprocess.run(args, cwd=MCP_DIR, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise SystemExit(f"wrangler falhou:\n{res.stdout}\n{res.stderr}")
        return res.stdout
    finally:
        if tmp:
            os.unlink(tmp)
