#!/usr/bin/env bash
# E2E local: sobe auth (8788) + mcp (8787) com estado descartável, cria um PAT e roda o smoke completo
# (OAuth com login dev + sessões MCP com OAuth e PAT) e o caminho cron → Queue → consumer.
# Uso: bash ci/e2e.sh        (no CI e localmente; as portas 8787/8788 precisam estar livres)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/.wrangler/e2e-state"
LOGS="$ROOT/.wrangler/e2e-logs"
export LF_PERSIST="$STATE"
rm -rf "$STATE" "$LOGS"
mkdir -p "$LOGS"

# .dev.vars: no CI vêm dos exemplos; localmente usa os seus se existirem.
[ -f "$ROOT/auth/.dev.vars" ] || sed 's/voce@exemplo.com/dev@example.com/' "$ROOT/auth/.dev.vars.example" > "$ROOT/auth/.dev.vars"
[ -f "$ROOT/mcp/.dev.vars" ] || cp "$ROOT/mcp/.dev.vars.example" "$ROOT/mcp/.dev.vars"
EMAIL="$(grep '^ALLOWED_EMAILS=' "$ROOT/auth/.dev.vars" | cut -d= -f2 | cut -d, -f1 | tr -d '\r ')"
[ -n "$EMAIL" ] || { echo "ALLOWED_EMAILS vazio em auth/.dev.vars"; exit 1; }

echo "== config (wrangler.jsonc gerados)"
(cd "$ROOT" && uv run --no-project ci/render_config.py)

PIDS=()
cleanup() {
  # wrangler/workerd são netos do subshell: é preciso derrubar a árvore inteira.
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] || continue
    if [ -r "/proc/$pid/winpid" ] && command -v taskkill >/dev/null; then   # Git Bash no Windows
      taskkill //F //T //PID "$(cat "/proc/$pid/winpid")" >/dev/null 2>&1 || true
    else
      kill "$pid" 2>/dev/null || true
    fi
  done
  if command -v pkill >/dev/null; then pkill -f -- "--persist-to $STATE" 2>/dev/null || true; fi   # Linux (CI)
}
trap cleanup EXIT

wait_for() { # url, nome, log
  for _ in $(seq 1 120); do
    curl -fsS -m 2 "$1" >/dev/null 2>&1 && { echo "✔ $2 no ar"; return 0; }
    sleep 1
  done
  echo "✘ $2 não subiu; log:"; tail -50 "$3"; exit 1
}

echo "== migrations (D1 local descartável)"
(cd "$ROOT/mcp" && npx --yes wrangler d1 migrations apply DB --local --persist-to "$STATE" >"$LOGS/migrations.log" 2>&1) \
  || { cat "$LOGS/migrations.log"; exit 1; }

echo "== subindo Workers"
(cd "$ROOT/auth" && exec npx wrangler dev --port 8788 --persist-to "$STATE" >"$LOGS/auth.log" 2>&1) &
PIDS+=($!)
(cd "$ROOT/mcp" && exec uv run pywrangler dev --port 8787 --persist-to "$STATE" >"$LOGS/mcp.log" 2>&1) &
PIDS+=($!)
wait_for http://localhost:8788/health auth "$LOGS/auth.log"
wait_for http://localhost:8787/health mcp "$LOGS/mcp.log"
# prontidão real: mcp alcança o auth via Service Binding (RPC) e o D1
wait_for "http://localhost:8787/health?deep=1" "mcp → auth (RPC) + D1" "$LOGS/mcp.log"

echo "== PAT de teste"
TOKEN="$(cd "$ROOT/mcp" && uv run scripts/create_token.py --email "$EMAIL" --name e2e | tail -1 | tr -d '\r ')"
[[ "$TOKEN" == lf_pat_* ]] || { echo "falha ao criar PAT: $TOKEN"; exit 1; }

echo "== smoke (OAuth + MCP)"
(cd "$ROOT/mcp" && uv run scripts/smoke.py --email "$EMAIL" --token "$TOKEN")

echo "== verificação pós-deploy contra o local (mesmo script do staging/production)"
(cd "$ROOT/mcp" && uv run scripts/check_deploy.py --url http://localhost:8787 --token "$TOKEN")

echo "== cron → Queue → consumer"
curl -fsS -X POST "http://127.0.0.1:8787/cdn-cgi/local/explorer/api/local/scheduled?worker=limafinance-local" \
  -H 'content-type: application/json' -d '{"cron":"0 3 * * *"}' >/dev/null
for _ in $(seq 1 30); do grep -q '"cron": "daily"' "$LOGS/mcp.log" && break; sleep 1; done
if grep -q '"cron": "daily"' "$LOGS/mcp.log"; then
  echo "✔ cron executou e enfileirou"
else
  echo "✘ cron não executou"; tail -30 "$LOGS/mcp.log"; exit 1
fi

if grep -q "had hung" "$LOGS/mcp.log"; then echo "✘ Worker pendurado (had hung) no log do mcp"; exit 1; fi
echo; echo "E2E OK"
