#!/usr/bin/env bash
# Volta os dois Workers do ambiente para a versão anterior (código). Migrations NÃO são revertidas:
# por isso toda migration precisa ser compatível com o código anterior (expand/contract, ver CLAUDE.md).
# Uso: bash ci/rollback.sh staging|production
set -uo pipefail
ENV="${1:?uso: rollback.sh staging|production}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MSG="rollback automático: verificação pós-deploy falhou (${GITHUB_SHA:-manual})"
status=0
(cd "$ROOT/mcp" && npx wrangler rollback --env "$ENV" -y -m "$MSG") || { echo "rollback do mcp falhou (primeiro deploy?)"; status=1; }
(cd "$ROOT/auth" && npx wrangler rollback --env "$ENV" -y -m "$MSG") || { echo "rollback do auth falhou (primeiro deploy?)"; status=1; }
exit $status
