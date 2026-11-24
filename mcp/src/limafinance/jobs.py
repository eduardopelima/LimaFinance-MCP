"""Jobs da fila `limafinance-jobs`. Todo job DEVE ser idempotente (a Queue entrega at-least-once).

Para um job novo: adicione um `type` e um ramo em `handle_job`.
"""

import json
from typing import Any

from .db import DB
from .domain.recurrences import materialize_month
from .lib.dates import month_of, today_in


async def enqueue_daily_jobs(env: Any) -> int:
    """Cron diário: um job por usuário com recorrência ativa (fan-out pela Queue)."""
    db = DB(env.DB)
    rows = await db.all(
        """SELECT DISTINCT r.user_id, u.timezone FROM recurrences r JOIN users u ON u.id = r.user_id
           WHERE r.deleted_at IS NULL"""
    )
    jobs = [{"type": "materialize_recurrences", "userId": r["user_id"], "month": month_of(today_in(r["timezone"]))} for r in rows]
    for i in range(0, len(jobs), 100):
        await env.JOBS.sendBatch([{"body": j} for j in jobs[i : i + 100]])
    return len(jobs)


async def handle_job(env: Any, job: dict) -> None:
    kind = job.get("type")
    if kind == "materialize_recurrences":
        n = await materialize_month(DB(env.DB), job["userId"], job["month"])
        print(json.dumps({"job": kind, "userId": job["userId"], "month": job["month"], "created": n}))
        return
    raise ValueError(f"job desconhecido: {kind}")


async def consume(env: Any, batch: Any) -> None:
    for msg in batch.messages:
        try:
            await handle_job(env, dict(msg.body))
            msg.ack()
        except Exception as e:
            print(f"job_failed: {msg.body} {e!r}")
            msg.retry({"delaySeconds": 60})  # após max_retries vai para a DLQ
