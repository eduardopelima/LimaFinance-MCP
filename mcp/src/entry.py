"""Entrypoint do Python Worker `limafinance`: /mcp (ASGI stateless), Queue consumer e Cron."""

import json
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from limafinance.jobs import consume, enqueue_daily_jobs
from limafinance.server import http_app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        path = url.path
        if path == "/health":
            return await self._health(deep="deep=1" in url.query)
        if path == "/mcp" and request.method in ("GET", "DELETE"):
            # Stateless: sem stream servidor→cliente (GET/SSE) nem sessão para encerrar (DELETE).
            # Sem isto, o GET SSE do cliente fica pendurado até o runtime cancelar ("code had hung").
            return Response("", status=405, headers={"Allow": "POST"})
        import asgi  # ponte ASGI ↔ Workers (Pyodide)

        # Uma app por requisição: stateless de verdade (nada compartilhado entre requisições).
        return await asgi.fetch(http_app(self.env), request.js_object, self.env)

    async def _health(self, deep: bool):
        """`/health` = o Worker responde. `/health?deep=1` = também alcança o auth (RPC) e o D1; 503 se não."""
        body = {"ok": True}
        if deep:
            try:
                body["auth"] = await self.env.AUTH.ping() == "pong"
            except Exception as e:
                body["auth"], body["auth_error"] = False, str(e)[:200]
            try:
                await self.env.DB.prepare("SELECT 1").first()
                body["db"] = True
            except Exception as e:
                body["db"], body["db_error"] = False, str(e)[:200]
            body["ok"] = body["auth"] and body["db"]
        return Response(json.dumps(body), status=200 if body["ok"] else 503, headers={"content-type": "application/json"})

    async def queue(self, batch, *args):
        await consume(self.env, batch)

    async def scheduled(self, controller, *args):
        n = await enqueue_daily_jobs(self.env)
        print(json.dumps({"cron": "daily", "enqueued": n}))
