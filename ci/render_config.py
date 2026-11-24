"""Gera auth/wrangler.jsonc e mcp/wrangler.jsonc a partir das bases versionadas + configuração do deploy.

    uv run --no-project ci/render_config.py                     # local (+ ambientes que estiverem configurados)
    uv run --no-project ci/render_config.py --require staging   # falha se staging não estiver completo (CI/deploy)

Fonte dos valores, em ordem de prioridade: variáveis de ambiente do processo (CI: GitHub Environment "Variables")
> arquivo `limafinance.env` (ou --env-file). Só stdlib: roda em qualquer Python 3.11+.

Um ambiente entra no arquivo gerado quando tem D1_ID_<ENV> e KV_ID_<ENV>. Sem nenhum, o resultado serve só para dev local.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENVS = ("staging", "production")
HEADER = "// GERADO por ci/render_config.py a partir de wrangler.base.jsonc + limafinance.env. NÃO edite; não vai para o git.\n"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.split(" #", 1)[0].strip().strip('"').strip("'")
    return values


def load_config(env_file: Path) -> dict[str, str]:
    cfg = read_env_file(env_file)
    for k, v in os.environ.items():
        if (k.startswith(("LF_", "CF_", "GOOGLE_", "D1_ID_", "KV_ID_"))) and v:
            cfg[k] = v
    return cfg


def strip_jsonc(text: str) -> str:
    # remove comentários // em linha própria (as bases só usam esse estilo)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def names(prefix: str, env: str) -> dict[str, str]:
    sfx = "" if env == "production" else f"-{env}"
    return {
        "mcp": f"{prefix}{sfx}",
        "auth": f"{prefix}-auth{sfx}",
        "d1": f"{prefix}{sfx}",
        "kv": f"{prefix}-oauth{sfx}",
        "queue": f"{prefix}-jobs{sfx}",
        "dlq": f"{prefix}-jobs{sfx}-dlq",
    }


def urls(cfg: dict[str, str], env: str) -> dict[str, str]:
    n = names(cfg["LF_PREFIX"], env)
    sub = cfg["CF_WORKERS_SUBDOMAIN"]
    return {
        "mcp_resource": f"https://{n['mcp']}.{sub}.workers.dev/mcp",
        "issuer": f"https://{n['auth']}.{sub}.workers.dev",
    }


def missing_for(cfg: dict[str, str], env: str) -> list[str]:
    up = env.upper()
    need = ["LF_PREFIX", "CF_WORKERS_SUBDOMAIN", "GOOGLE_CLIENT_ID", f"D1_ID_{up}", f"KV_ID_{up}"]
    return [k for k in need if not cfg.get(k)]


def validate(cfg: dict[str, str]) -> None:
    prefix = cfg.get("LF_PREFIX", "")
    if prefix and not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", prefix):
        raise SystemExit(f"LF_PREFIX inválido ({prefix!r}): use minúsculas, dígitos e hífen (2–31 chars).")
    sub = cfg.get("CF_WORKERS_SUBDOMAIN", "")
    if sub and not re.fullmatch(r"[a-z0-9-]+", sub):
        raise SystemExit(f"CF_WORKERS_SUBDOMAIN inválido ({sub!r}): só o subdomínio, sem '.workers.dev'.")


def auth_env_block(cfg: dict[str, str], env: str) -> dict:
    n, u = names(cfg["LF_PREFIX"], env), urls(cfg, env)
    return {
        "name": n["auth"],
        "vars": {"ISSUER": u["issuer"], "MCP_RESOURCE": u["mcp_resource"], "GOOGLE_CLIENT_ID": cfg["GOOGLE_CLIENT_ID"], "DEV_LOGIN": "false"},
        "kv_namespaces": [{"binding": "OAUTH_KV", "id": cfg[f"KV_ID_{env.upper()}"]}],
    }


def mcp_env_block(cfg: dict[str, str], env: str) -> dict:
    n, u = names(cfg["LF_PREFIX"], env), urls(cfg, env)
    return {
        "name": n["mcp"],
        "vars": {"MCP_RESOURCE": u["mcp_resource"], "AUTH_ISSUER": u["issuer"]},
        "services": [{"binding": "AUTH", "service": n["auth"]}],
        "d1_databases": [{"binding": "DB", "database_name": n["d1"], "database_id": cfg[f"D1_ID_{env.upper()}"], "migrations_dir": "../migrations"}],
        "queues": {
            "producers": [{"binding": "JOBS", "queue": n["queue"]}],
            "consumers": [{"queue": n["queue"], "max_batch_size": 10, "max_batch_timeout": 5, "max_retries": 3, "dead_letter_queue": n["dlq"]}],
        },
        "triggers": {"crons": ["0 3 * * *"]},
    }


def render(cfg: dict[str, str], envs: list[str]) -> dict[str, dict]:
    out = {}
    for worker, block in (("auth", auth_env_block), ("mcp", mcp_env_block)):
        base = json.loads(strip_jsonc((ROOT / worker / "wrangler.base.jsonc").read_text(encoding="utf-8")))
        if cfg.get("CF_ACCOUNT_ID"):
            base["account_id"] = cfg["CF_ACCOUNT_ID"]
        if envs:
            base["env"] = {e: block(cfg, e) for e in envs}
        out[worker] = base
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env-file", default=str(ROOT / "limafinance.env"))
    p.add_argument("--require", action="append", choices=ENVS, default=[], help="ambiente que precisa estar completo")
    p.add_argument("--print", action="store_true", help="mostra nomes/URLs dos ambientes configurados")
    a = p.parse_args(argv)

    cfg = load_config(Path(a.env_file))
    cfg.setdefault("LF_PREFIX", "limafinance")
    validate(cfg)
    for e in a.require:
        if miss := missing_for(cfg, e):
            print(f"✘ ambiente {e} incompleto; faltam: {', '.join(miss)} (em {a.env_file} ou nas Variables do GitHub)", file=sys.stderr)
            return 1
    envs = [e for e in ENVS if not missing_for(cfg, e)]
    for worker, data in render(cfg, envs).items():
        (ROOT / worker / "wrangler.jsonc").write_text(HEADER + json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"✔ wrangler.jsonc gerados (local{''.join(' + ' + e for e in envs)})")
    if a.print:
        for e in envs:
            n, u = names(cfg["LF_PREFIX"], e), urls(cfg, e)
            print(f"  [{e}] mcp={n['mcp']} auth={n['auth']}\n          MCP: {u['mcp_resource']}\n          auth: {u['issuer']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
