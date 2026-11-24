import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..db import DB, Stmt

SCOPE_READ = "mcp:read"
SCOPE_WRITE = "mcp:write"


@dataclass(frozen=True)
class Actor:
    """Quem está agindo. Montado a partir do token (OAuth ou PAT); nunca de input do LLM."""

    user_id: str
    email: str
    timezone: str
    actor: str  # "oauth:<client_id>", "pat:<nome>", "job:<nome>", "import"
    scopes: tuple[str, ...] = field(default_factory=tuple)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def audit_stmt(db: DB, a: Actor, action: str, entity_id: str, payload: Any = None) -> Stmt:
    return db.stmt(
        "INSERT INTO audit_log (user_id, actor, action, entity_id, payload) VALUES (?, ?, ?, ?, ?)",
        a.user_id,
        a.actor,
        action,
        entity_id,
        None if payload is None else json.dumps(payload, ensure_ascii=False, default=str),
    )
