"""Resource server: valida o bearer token de cada requisição /mcp.

- `lf_pat_...` → personal access token, verificado aqui contra D1 (só o SHA-256 é guardado).
- qualquer outro → token OAuth emitido pelo Worker TS `limafinance-auth`, verificado via RPC
  (`env.AUTH.validateToken(resource, token)`, Service Binding; não passa pela internet).

O resultado vira `AccessToken` do SDK MCP; `claims` carrega a identidade usada para montar o `Actor`.
"""

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from .db import DB
from .domain.context import SCOPE_READ, SCOPE_WRITE, Actor
from .lib.dates import DEFAULT_TZ
from .lib.errors import DomainError
from .lib.ids import new_id, sha256_hex

PAT_PREFIX = "lf_pat_"


async def upsert_user(db: DB, email: str, name: str | None = None) -> dict[str, Any]:
    normalized = email.strip().lower()
    await db.run("INSERT INTO users (id, email, name) VALUES (?, ?, ?) ON CONFLICT (email) DO NOTHING", new_id("usr"), normalized, name)
    row = await db.first("SELECT id, email, timezone FROM users WHERE email = ?", normalized)
    assert row is not None
    return row


class LimaTokenVerifier:
    def __init__(self, env: Any, resource: str):
        self._env = env
        self._db = DB(env.DB)
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            if token.startswith(PAT_PREFIX):
                return await self._verify_pat(token)
            return await self._verify_oauth(token)
        except Exception as e:  # falha fecha: nunca autentica em caso de erro
            print(f"auth_error: {type(e).__name__}: {str(e)[:300]}")  # mensagem sem o token
            return None

    async def _verify_pat(self, token: str) -> AccessToken | None:
        row = await self._db.first(
            """SELECT t.id, t.name, t.scopes, u.id AS user_id, u.email, u.timezone
               FROM api_tokens t JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.revoked_at IS NULL
                 AND (t.expires_at IS NULL OR t.expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
            sha256_hex(token),
        )
        if not row:
            return None
        await self._db.run("UPDATE api_tokens SET last_used_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", row["id"])
        scopes = [s for s in row["scopes"].split() if s in (SCOPE_READ, SCOPE_WRITE)]
        return AccessToken(
            token=token,
            client_id=f"pat:{row['name']}",
            scopes=scopes,
            resource=self._resource,
            subject=row["user_id"],
            claims={"email": row["email"], "timezone": row["timezone"] or DEFAULT_TZ},
        )

    async def _verify_oauth(self, token: str) -> AccessToken | None:
        v = await self._env.AUTH.validateToken(self._resource, token)
        if not v:
            return None
        user = await upsert_user(self._db, v["email"], v.get("name"))
        return AccessToken(
            token=token,
            client_id=str(v["clientId"]),
            scopes=list(v["scope"]),
            expires_at=int(v["expiresAt"]) if v.get("expiresAt") else None,
            resource=v["audience"],
            subject=user["id"],
            claims={"email": user["email"], "timezone": user["timezone"] or DEFAULT_TZ},
        )


def current_actor() -> Actor:
    tok = get_access_token()
    if tok is None or not tok.subject:
        raise DomainError("unauthenticated", "Sem identidade autenticada.")
    claims = tok.claims or {}
    prefix = "" if tok.client_id.startswith("pat:") else "oauth:"
    return Actor(
        user_id=tok.subject,
        email=claims.get("email", ""),
        timezone=claims.get("timezone") or DEFAULT_TZ,
        actor=f"{prefix}{tok.client_id}",
        scopes=tuple(tok.scopes),
    )
