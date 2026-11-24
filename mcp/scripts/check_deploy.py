"""Verificação pós-deploy (staging/production). NUNCA escreve dados.

    uv run scripts/check_deploy.py --env staging [--token lf_pat_...]   # token: PAT SÓ-LEITURA (opcional)
    uv run scripts/check_deploy.py --url https://limafinance.x.workers.dev   # URL explícita

Checa: /health; /mcp sem token → 401 com resource_metadata; metadata RFC 9728 aponta para o AS esperado;
AS anuncia DCR, PKCE S256 e CIMD. Com --token: sessão MCP real (14 tools, get_context, month_summary).
Tenta de novo por até ~60 s (deploy recém-publicado pode levar alguns segundos para propagar).
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import httpx

MCP_DIR = Path(__file__).resolve().parent.parent


def env_vars(env: str) -> dict:
    """Lê vars do ambiente em mcp/wrangler.jsonc (comentários // em linha própria)."""
    text = (MCP_DIR / "wrangler.jsonc").read_text(encoding="utf-8")
    cfg = json.loads("\n".join(line for line in text.splitlines() if not line.strip().startswith("//")))
    return cfg["env"][env]["vars"]


p = argparse.ArgumentParser()
p.add_argument("--env", choices=["staging", "production"])
p.add_argument("--url", help="base do Worker MCP (sem /mcp)")
p.add_argument("--token", help="PAT só-leitura para testar uma sessão MCP real")
a = p.parse_args()
if a.env:
    v = env_vars(a.env)
    resource, issuer = v["MCP_RESOURCE"], v["AUTH_ISSUER"]
elif a.url:
    resource, issuer = a.url.rstrip("/") + "/mcp", None
else:
    raise SystemExit("Informe --env ou --url")
base = resource.removesuffix("/mcp")
if "<" in resource:
    raise SystemExit(f"MCP_RESOURCE do ambiente ainda tem placeholder: {resource}")

failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("✔ " if ok else "✘ ") + msg)
    if not ok:
        failures.append(msg)


def wait_healthy(h: httpx.Client) -> bool:
    for _ in range(12):
        try:
            r = h.get(f"{base}/health")
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(5)
    return False


with httpx.Client(timeout=30) as h:
    check(wait_healthy(h), f"{base}/health → 200")
    r = h.post(resource, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
               headers={"accept": "application/json, text/event-stream"})
    www = r.headers.get("www-authenticate", "")
    check(r.status_code == 401 and "resource_metadata=" in www, "/mcp sem token → 401 + resource_metadata")
    m = re.search(r'resource_metadata="([^"]+)"', www)
    prm = h.get(m.group(1)).json() if m else {}
    check(prm.get("resource") == resource, f"metadata RFC 9728: resource = {prm.get('resource')}")
    as_url = (prm.get("authorization_servers") or [None])[0]
    if issuer:
        check(as_url == issuer, f"authorization server = {as_url}")
    asm = h.get(f"{as_url}/.well-known/oauth-authorization-server").json() if as_url else {}
    check(asm.get("issuer") == as_url, "AS metadata: issuer confere")
    check(bool(asm.get("registration_endpoint")), "AS: DCR disponível (ChatGPT, Gemini CLI)")
    check(asm.get("client_id_metadata_document_supported") is True, "AS: CIMD disponível (Claude)")
    check("S256" in asm.get("code_challenge_methods_supported", []), "AS: PKCE S256")
    check(resource in asm.get("protected_resources", [resource]), "AS emite tokens para este resource")
    deep = h.get(f"{base}/health?deep=1")
    check(deep.status_code == 200 and deep.json().get("auth") is True and deep.json().get("db") is True,
          f"/health?deep=1: mcp → auth (RPC) e D1 ok ({deep.text[:120]})")
    auth_health = h.get(f"{as_url}/health") if as_url else None
    check(auth_health is not None and auth_health.status_code == 200, "auth /health → 200")


async def mcp_session(token: str) -> None:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(resource, http_client=http)) as c:
        tools = (await c.list_tools()).tools
        check(len(tools) == 14, f"[pat] tools/list: {len(tools)} tools")
        ctx = await c.call_tool("get_context", {})
        check(not ctx.is_error, f"[pat] get_context: {json.loads(ctx.content[0].text).get('today') if not ctx.is_error else ctx.content[0].text}")
        s = await c.call_tool("month_summary", {})
        check(not s.is_error, "[pat] month_summary")
    await http.aclose()


if a.token:
    asyncio.run(mcp_session(a.token))
else:
    print("· sessão MCP não testada (sem --token / secret SMOKE_TOKEN)")

if failures:
    raise SystemExit(f"\n{len(failures)} falha(s) na verificação pós-deploy.")
print("\nDeploy verificado.")
