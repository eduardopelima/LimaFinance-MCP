"""Cria um personal access token (bearer fixo) para Claude Code / Gemini CLI / MCP Inspector.

    uv run scripts/create_token.py --email voce@exemplo.com --name claude-code [--read-only] [--env local|staging|production] [--days 365]

O token é exibido UMA vez; o banco guarda só o SHA-256. Cria o usuário se ainda não existir.
"""

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")  # console do Windows (cp1252)
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from d1 import d1_execute, random_id, sql

p = argparse.ArgumentParser()
p.add_argument("--email", required=True)
p.add_argument("--name", required=True)
p.add_argument("--read-only", action="store_true")
p.add_argument("--env", default="local", choices=["local", "staging", "production"])
p.add_argument("--days", type=int)
a = p.parse_args()

email = a.email.strip().lower()
scopes = "mcp:read" if a.read_only else "mcp:read mcp:write"
expires = (datetime.now(timezone.utc) + timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%S.000Z") if a.days else None
token = "lf_pat_" + secrets.token_urlsafe(32)
token_hash = hashlib.sha256(token.encode()).hexdigest()

d1_execute(
    a.env,
    command=f"""INSERT INTO users (id, email) VALUES ({sql(random_id('usr'))}, {sql(email)}) ON CONFLICT (email) DO NOTHING;
INSERT INTO api_tokens (id, user_id, name, token_hash, scopes, expires_at)
SELECT {sql(random_id('tok'))}, id, {sql(a.name)}, {sql(token_hash)}, {sql(scopes)}, {sql(expires)} FROM users WHERE email = {sql(email)};""",
)
print(f"\nToken criado ({a.env}) para {email} [{scopes}]{f', expira em {expires}' if expires else ''}.")
print("Guarde agora — ele não será mostrado de novo:\n")
print(token)
