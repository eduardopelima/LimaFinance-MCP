"""Smoke test ponta a ponta contra os dois Workers rodando (padrão: wrangler dev local).

    uv run scripts/smoke.py [--url http://localhost:8787] [--email dev@example.com] [--token lf_pat_...]

1. /mcp sem token → 401 + resource_metadata → metadata do AS (outro Worker) com DCR e PKCE.
2. OAuth completo, como um cliente de IA faria: DCR → /authorize (consentimento) → login dev (só localhost)
   → code → /oauth/token com PKCE → access_token (+ refresh).
3. Cliente MCP real (SDK Python) com o access_token: tools/list, get_context, month_summary.
4. (opcional) o mesmo com um PAT.
"""

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")  # console do Windows (cp1252)
import asyncio
import base64
import hashlib
import json
import re
import secrets
import sys

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://localhost:8787")
p.add_argument("--email", default="dev@example.com")
p.add_argument("--token")
a = p.parse_args()

BASE = a.url.rstrip("/")
REDIRECT = "http://localhost:53682/callback"
failures = 0


def check(ok: bool, msg: str) -> None:
    global failures
    print(("✔ " if ok else "✘ ") + msg)
    failures += 0 if ok else 1


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def oauth_flow(http: httpx.Client) -> dict:
    r = http.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                  headers={"accept": "application/json, text/event-stream"})
    check(r.status_code == 401 and "resource_metadata=" in r.headers.get("www-authenticate", ""), "401 + WWW-Authenticate com resource_metadata")
    prm = http.get(f"{BASE}/.well-known/oauth-protected-resource/mcp").json()
    asm = http.get(f"{prm['authorization_servers'][0]}/.well-known/oauth-authorization-server").json()
    check(bool(asm.get("registration_endpoint")) and "S256" in asm.get("code_challenge_methods_supported", []),
          f"AS em outro Worker ({prm['authorization_servers'][0]}) com DCR e PKCE S256")
    check(asm.get("client_id_metadata_document_supported") is True, "AS anuncia CIMD (Claude)")

    reg = http.post(asm["registration_endpoint"], json={
        "client_name": "limafinance smoke test", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none",
    }).json()
    check("client_id" in reg, "DCR: client registrado")

    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    state = b64url(secrets.token_bytes(8))
    params = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT, "code_challenge": challenge,
              "code_challenge_method": "S256", "state": state, "scope": "mcp:read mcp:write offline_access", "resource": prm["resource"]}

    def consent(email: str) -> httpx.Response:
        with httpx.Client(follow_redirects=False) as browser:
            page = browser.get(asm["authorization_endpoint"], params=params)
            m = re.search(r'name="handle" value="([^"]+)"', page.text)
            if email == a.email:
                check(page.status_code == 200 and m is not None and "limafinance smoke test" in page.text, "página de consentimento com handle e nome do cliente")
                check(page.headers.get("x-frame-options") == "DENY", "consentimento não pode ser emoldurado")
            # O cookiejar do Python não reenvia cookies Secure/__Host- por http (navegadores aceitam em localhost):
            # repassamos o cookie de vínculo do consentimento à mão, como o navegador faria.
            cookie = "; ".join(c.split(";", 1)[0] for c in page.headers.get_list("set-cookie"))
            return browser.post(asm["authorization_endpoint"], headers={"cookie": cookie},
                                data={"handle": m.group(1) if m else "", "method": "dev", "email": email, "scope": "mcp:write"})

    approved = consent(a.email)
    loc = httpx.URL(approved.headers.get("location", "http://x/"))
    code = loc.params.get("code")
    check(approved.status_code == 302 and bool(code) and loc.params.get("state") == state, "login dev + aprovação → redirect com code e state")
    check(consent("intruso@example.com").status_code == 403, "e-mail fora da allowlist → 403")

    tok = http.post(asm["token_endpoint"], data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                                 "client_id": reg["client_id"], "code_verifier": verifier, "resource": prm["resource"]}).json()
    check("access_token" in tok and "refresh_token" in tok, f"token: scope=\"{tok.get('scope')}\", expires_in={tok.get('expires_in')}")
    ref = http.post(asm["token_endpoint"], data={"grant_type": "refresh_token", "refresh_token": tok.get("refresh_token", ""), "client_id": reg["client_id"]}).json()
    check("access_token" in ref, "refresh_token → novo access_token")
    return ref if "access_token" in ref else tok


async def mcp_session(label: str, bearer: str) -> None:
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {bearer}"})
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as c:
        tools = (await c.list_tools()).tools
        check(len(tools) == 14, f"[{label}] tools/list: {len(tools)} tools")
        ro = sorted(t.name for t in tools if t.annotations and t.annotations.read_only_hint)
        check(ro == sorted(["get_context", "list_categories", "list_transactions", "month_summary", "list_recurrences"]),
              f"[{label}] readOnlyHint só nas de leitura")
        ctx = await c.call_tool("get_context", {})
        data = json.loads(ctx.content[0].text)
        check(not ctx.is_error and data["user"] == a.email.lower(), f"[{label}] get_context: hoje={data['today']} ({data['timezone']})")
        s = await c.call_tool("month_summary", {})
        check(not s.is_error, f"[{label}] month_summary: saldo {json.loads(s.content[0].text)['saldo_do_mes']}")
        # Escrita idempotente e reversível num mês distante (não suja os dados reais).
        w = await c.call_tool("set_budget", {"month": "12/2099", "amount": "1,00"})
        u = await c.call_tool("set_budget", {"month": "12/2099", "remove": True})
        check(not w.is_error and not u.is_error, f"[{label}] escrita com escopo mcp:write (set_budget + remove)")
    await http.aclose()


with httpx.Client(timeout=60) as h:
    access = oauth_flow(h)["access_token"]
    r = h.get(f"{BASE}/mcp", headers={"Authorization": f"Bearer {access}"})
    check(r.status_code == 405, "GET /mcp → 405 (stateless, sem stream SSE pendurado)")
asyncio.run(mcp_session("oauth", access))
if a.token:
    asyncio.run(mcp_session("pat", a.token))
else:
    print("· PAT não testado (passe --token lf_pat_...)")

print(f"\n{failures} falha(s)." if failures else "\nTudo certo.")
sys.exit(1 if failures else 0)
