"""Deploy your own: publica o limafinance na SUA conta Cloudflare a partir de `limafinance.env`.

    cp limafinance.env.example limafinance.env        # preencha LF_PREFIX e CF_WORKERS_SUBDOMAIN
    uv run --no-project ci/deploy_your_own.py [--env production|staging]

Pode rodar quantas vezes quiser: cada passo verifica antes de agir e pula o que já está pronto.
Quando precisa de algo que só você pode fazer (login, OAuth do Google, segredos), imprime uma linha
`NEEDS: ...` com o comando exato e sai com código 2. Faça o que ela pede e rode de novo.

Requisitos: Node 22+ e uv (o deploy do Worker Python usa pywrangler). Só stdlib aqui.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ci"))
import render_config as rc  # noqa: E402


class Needs(Exception):
    """Algo que só o humano pode fazer. Mensagem = instrução exata."""


def run(args: list[str], cwd: Path = ROOT / "auth", check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    exe = shutil.which(args[0]) or args[0]
    res = subprocess.run([exe, *args[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})
    if check and res.returncode != 0:
        raise SystemExit(f"✘ falhou: {' '.join(args)}\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    return res


def wrangler(*args: str, cwd: Path = ROOT / "auth", check: bool = True) -> subprocess.CompletedProcess:
    return run(["npx", "--yes", "wrangler", *args], cwd=cwd, check=check)


def json_out(text: str):
    """Extrai o primeiro JSON da saída (o wrangler às vezes imprime banners antes)."""
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"sem JSON na saída: {text[:300]}")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def step(msg: str) -> None:
    print(f"\n== {msg}")


# ------------------------------------------------------------------------------------------------

def check_tools() -> None:
    step("ferramentas")
    for tool, hint in (("npx", "instale Node 22+ (nodejs.org)"), ("uv", "instale uv: https://docs.astral.sh/uv/")):
        if not shutil.which(tool):
            raise Needs(f"{tool} não encontrado: {hint}")
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=ROOT / "auth")
    run(["uv", "sync", "--locked"], cwd=ROOT / "mcp")
    rc.main([])  # gera os wrangler.jsonc (ao menos o local) para os comandos do wrangler terem config
    print("✔ node, uv e dependências")


def check_login(cfg: dict, env_file: Path) -> None:
    step("login na Cloudflare")
    res = wrangler("whoami", "--json", check=False)
    try:
        who = json_out(res.stdout)
    except ValueError:
        who = {}
    if not who.get("loggedIn"):
        raise Needs("faça login na Cloudflare: npx wrangler login   (abre o navegador; depois rode este script de novo)")
    accounts = who.get("accounts") or []
    if not cfg.get("CF_ACCOUNT_ID"):
        if len(accounts) == 1 and accounts[0].get("id"):
            cfg["CF_ACCOUNT_ID"] = accounts[0]["id"]
            update_env_file(env_file, {"CF_ACCOUNT_ID": cfg["CF_ACCOUNT_ID"]})
        elif len(accounts) > 1:
            opts = ", ".join(f"{a.get('name')}={a.get('id')}" for a in accounts)
            raise Needs(f"você tem várias contas; defina CF_ACCOUNT_ID em {env_file.name} ({opts})")
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = cfg.get("CF_ACCOUNT_ID", "")
    print(f"✔ logado como {who.get('email', '?')} (conta {cfg.get('CF_ACCOUNT_ID') or '?'})")


def check_basics(cfg: dict, env_file: Path) -> None:
    rc.validate(cfg)
    if not cfg.get("CF_WORKERS_SUBDOMAIN"):
        raise Needs(f"defina CF_WORKERS_SUBDOMAIN em {env_file.name}: dash.cloudflare.com → Workers & Pages → 'Subdomain' "
                    "(o trecho antes de .workers.dev; se não existir, escolha um lá)")


def ensure_resources(cfg: dict, env: str, env_file: Path) -> None:
    step(f"recursos Cloudflare ({env})")
    n, up = rc.names(cfg["LF_PREFIX"], env), env.upper()
    updates: dict[str, str] = {}

    if not cfg.get(f"D1_ID_{up}"):
        dbs = json_out(wrangler("d1", "list", "--json").stdout)
        found = next((d for d in dbs if d.get("name") == n["d1"]), None)
        if not found:
            wrangler("d1", "create", n["d1"])
            found = next(d for d in json_out(wrangler("d1", "list", "--json").stdout) if d.get("name") == n["d1"])
            print(f"✔ D1 {n['d1']} criado")
        updates[f"D1_ID_{up}"] = found.get("uuid") or found.get("id")
    print(f"✔ D1 {n['d1']}")

    if not cfg.get(f"KV_ID_{up}"):
        kvs = json_out(wrangler("kv", "namespace", "list").stdout)
        found = next((k for k in kvs if k.get("title") == n["kv"]), None)
        if not found:
            wrangler("kv", "namespace", "create", n["kv"])
            found = next(k for k in json_out(wrangler("kv", "namespace", "list").stdout) if k.get("title") == n["kv"])
            print(f"✔ KV {n['kv']} criado")
        updates[f"KV_ID_{up}"] = found["id"]
    print(f"✔ KV {n['kv']}")

    for q in (n["queue"], n["dlq"]):
        res = wrangler("queues", "create", q, check=False)
        out = (res.stdout + res.stderr).lower()
        if res.returncode != 0 and "already" not in out and "exists" not in out:
            raise SystemExit(f"✘ falha ao criar a fila {q}:\n{res.stdout}\n{res.stderr}")
        print(f"✔ fila {q}")

    if updates:
        cfg.update(updates)
        update_env_file(env_file, updates)
        print(f"✔ IDs gravados em {env_file.name}")


def check_google(cfg: dict, env: str, env_file: Path) -> None:
    step("Google OAuth")
    if cfg.get("GOOGLE_CLIENT_ID"):
        print("✔ GOOGLE_CLIENT_ID definido")
        return
    u = rc.urls(cfg, env)
    raise Needs(
        "crie um OAuth client do Google e cole o Client ID em GOOGLE_CLIENT_ID no "
        f"{env_file.name}:\n"
        "   console.cloud.google.com → APIs & Services → Credentials → Create credentials → OAuth client ID → Web application\n"
        f"   Authorized redirect URI: {u['issuer']}/oauth/google/callback\n"
        "   Opcional: http://localhost:8788/oauth/google/callback, só para testar o login Google com o auth local\n"
        "   (no dev local o login dev já basta).\n"
        "   NÃO marque 'cliente para agentes de IA': o Google aqui só identifica o usuário; o agente recebe token do limafinance.\n"
        "   Se pedir 'OAuth consent screen': tipo External, adicione seu e-mail como test user."
    )


def deploy(env: str) -> None:
    step(f"config + migrations + deploy ({env})")
    if rc.main(["--require", env]) != 0:
        raise SystemExit(1)
    wrangler("d1", "migrations", "apply", "DB", "--remote", "--env", env, cwd=ROOT / "mcp")
    print("✔ migrations")
    wrangler("deploy", "--env", env, cwd=ROOT / "auth")
    print("✔ auth publicado")
    run(["uv", "run", "pywrangler", "deploy", "--env", env], cwd=ROOT / "mcp")
    print("✔ mcp publicado")


def check_secrets(env: str) -> None:
    step("segredos do Worker de auth")
    res = wrangler("secret", "list", "--env", env, "--format", "json", check=False)
    try:
        have = {s.get("name") for s in json_out(res.stdout)}
    except ValueError:
        have = set()
    missing = [s for s in ("GOOGLE_CLIENT_SECRET", "ALLOWED_EMAILS") if s not in have]
    if missing:
        cmds = "\n".join(f"   cd auth && npx wrangler secret put {s} --env {env}" for s in missing)
        raise Needs(
            "defina os segredos (você digita o valor no prompt oculto; ele não passa por este script):\n"
            f"{cmds}\n"
            "   ALLOWED_EMAILS = e-mails Google autorizados, separados por vírgula (ex.: voce@gmail.com)"
        )
    print("✔ GOOGLE_CLIENT_SECRET e ALLOWED_EMAILS definidos")


def verify(env: str) -> None:
    step(f"verificação pós-deploy ({env})")
    res = run(["uv", "run", "scripts/check_deploy.py", "--env", env], cwd=ROOT / "mcp", check=False)
    print(res.stdout.strip())
    if res.returncode != 0:
        raise SystemExit("✘ a verificação falhou (veja acima). O deploy está no ar, mas algo não responde como esperado.")


def summary(cfg: dict, env: str) -> None:
    u = rc.urls(cfg, env)
    print(f"""
✔ Pronto. limafinance ({env}) no ar.

  Endpoint MCP:  {u['mcp_resource']}

  Claude (web/desktop): Configurações → Conectores → Adicionar conector personalizado → URL acima (login Google)
  ChatGPT:              Configurações → Apps/Conectores → modo desenvolvedor → URL acima (OAuth)
  Claude Code (PAT):    cd mcp && uv run scripts/create_token.py --email voce@gmail.com --name claude-code --env {env}
                        claude mcp add --transport http limafinance {u['mcp_resource']} --header "Authorization: Bearer <token>"
""")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", choices=rc.ENVS, default="production")
    p.add_argument("--env-file", default=str(ROOT / "limafinance.env"))
    a = p.parse_args()
    env_file = Path(a.env_file)
    if not env_file.exists():
        shutil.copy(ROOT / "limafinance.env.example", env_file)
        print(f"· criei {env_file.name} a partir do exemplo")
    cfg = rc.load_config(env_file)
    cfg.setdefault("LF_PREFIX", "limafinance")
    try:
        check_tools()
        check_basics(cfg, env_file)
        check_login(cfg, env_file)
        ensure_resources(cfg, a.env, env_file)
        check_google(cfg, a.env, env_file)
        deploy(a.env)
        check_secrets(a.env)
        verify(a.env)
    except Needs as e:
        print(f"\nNEEDS: {e}")
        return 2
    summary(cfg, a.env)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
