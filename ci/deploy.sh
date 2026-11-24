#!/usr/bin/env bash
# Deploy de um ambiente: config → migrations → auth → mcp. Ordem importa:
#  1. config gerada de limafinance.env / variáveis do CI (falha se o ambiente estiver incompleto)
#  2. migrations antes do código (regra: toda migration é compatível com o código JÁ publicado; ver CLAUDE.md)
#  3. auth antes do mcp (o mcp tem Service Binding para o auth)
# Uso: bash ci/deploy.sh staging|production      (requer CLOUDFLARE_API_TOKEN ou `wrangler login`)
set -euo pipefail
ENV="${1:?uso: deploy.sh staging|production}"
[[ "$ENV" == staging || "$ENV" == production ]] || { echo "ambiente inválido: $ENV"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== [$ENV] config"
(cd "$ROOT" && uv run --no-project ci/render_config.py --require "$ENV" --print)

echo "== [$ENV] migrations D1"
(cd "$ROOT/mcp" && npx wrangler d1 migrations apply DB --remote --env "$ENV")

echo "== [$ENV] deploy auth"
(cd "$ROOT/auth" && npx wrangler deploy --env "$ENV")

echo "== [$ENV] deploy mcp (Python)"
(cd "$ROOT/mcp" && uv run pywrangler deploy --env "$ENV")
