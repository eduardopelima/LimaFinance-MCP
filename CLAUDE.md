# limafinance: guia para agentes

Servidor MCP remoto que substitui uma planilha de controle financeiro. O usuário conversa com Claude, ChatGPT ou
Gemini CLI, e o LLM chama as tools daqui. Não existe app nem site.
Arquitetura e decisões: `docs/ARCHITECTURE.md`. Setup e clientes: `README.md`.

## Dois Workers

| Pasta | Worker | Linguagem | Papel |
|---|---|---|---|
| `mcp/` | `limafinance` | **Python** (Pyodide) | `/mcp` (todas as tools), D1, Queue, Cron, PAT, import. **Quase tudo acontece aqui.** |
| `auth/` | `limafinance-auth` | TypeScript | Só o authorization server OAuth 2.1 (`workers-oauth-provider`, que não existe em Python). Não toca em dados. |

O Python valida tokens OAuth via RPC: `env.AUTH.validateToken(MCP_RESOURCE, token)` (Service Binding, nunca pela internet).
`migrations/` (raiz) é do D1, usado só pelo `mcp/`. Mudou algo em `auth/`? Mantenha-o pequeno: identidade, consentimento, tokens.

## Comandos

```sh
# terminal 1: auth (porta 8788)
cd auth && npm install && npx wrangler dev --port 8788 --persist-to ../.wrangler/state
# terminal 2: mcp (porta 8787)
cd mcp && uv sync && uv run pywrangler dev --port 8787 --persist-to ../.wrangler/state

cd mcp && uv run pytest                      # testes (lib, resolução de categoria, parser da planilha)
cd mcp && uv run scripts/smoke.py --token lf_pat_...   # e2e: OAuth entre os 2 Workers + MCP (OAuth e PAT)
cd mcp && uv run scripts/create_token.py --email x@y --name claude-code [--read-only] [--env local|staging|production]
cd mcp && npx wrangler d1 migrations apply limafinance --local --persist-to ../.wrangler/state
uv run --no-project ci/render_config.py     # gera auth/ e mcp/wrangler.jsonc (rode após clonar e após mudar a base/.env)
cd auth && npx tsc --noEmit                  # typecheck do auth (rode `npx wrangler types` após gerar a config)
npx @modelcontextprotocol/inspector --cli http://localhost:8787/mcp --transport http \
  --header "Authorization: Bearer lf_pat_..." --method tools/list
# disparar o cron localmente (Python Worker não tem /__scheduled):
curl -X POST "http://127.0.0.1:8787/cdn-cgi/local/explorer/api/local/scheduled?worker=limafinance-local" -d '{"cron":"0 3 * * *"}'
```

`--persist-to ../.wrangler/state` nos dois: o estado local (D1/KV) fica num lugar só.
Antes de dizer que terminou: `uv run pytest` e, se mexeu em auth/MCP, `bash ci/e2e.sh` (é exatamente o que o CI roda;
exige as portas 8787/8788 livres, então pare os `wrangler dev` antes).

## CI/CD (`.github/workflows/pipeline.yml`)

- **PR**: `test` (pytest + typecheck do auth + JSON das configs), `e2e` (`ci/e2e.sh`: os dois Workers locais, OAuth completo,
  MCP com OAuth e PAT, `check_deploy.py` contra o local, cron → Queue) e `bundle` (dry-run do deploy Python, limite de 64 MiB).
- **push na `main`**: os mesmos jobs → `deploy-staging` → `deploy-production` (**automático**, só se staging passar).
  Cada deploy: `ci/deploy.sh <env>` (gera a config com `--require <env>` → migrations → auth → mcp) → `scripts/check_deploy.py --env <env>`.
  Os jobs de deploy só rodam com a variável de repositório `DEPLOY_ENABLED=true` (forks ficam só com CI).
  Falhou a verificação → `ci/rollback.sh <env>` volta o **código** dos dois Workers.
- **Configuração por deploy, nunca no código.** `*/wrangler.base.jsonc` (versionado) + `limafinance.env` (local, fora do git)
  ou Variables do GitHub Environment (CI) → `ci/render_config.py` gera `auth/wrangler.jsonc` e `mcp/wrangler.jsonc` (fora do git).
  Edite a base, nunca o gerado. Nomes derivam de `LF_PREFIX` e as URLs de `CF_WORKERS_SUBDOMAIN` (`render_config.names/urls`).
  Não escreva account IDs, URLs, subdomínio ou e-mails em arquivo versionado: o repositório é público.
- Nunca rode `wrangler deploy` sem `--env`. Instância nova do zero: `ci/deploy_your_own.py` (para com `NEEDS:` e exit 2
  quando precisa de humano; é seguro rodar de novo).
- **Migrations não têm rollback.** Como produção é automática, toda migration precisa ser compatível com o código que já está no ar
  (expand/contract): adicionar coluna nullable ou com default e tabela/índice nova pode; renomear/apagar coluna só em duas
  entregas (1ª para de usar, 2ª remove). Nunca `DROP`/`RENAME` na mesma entrega que muda o código.
- `/health` = vivo; `/health?deep=1` = alcança o auth (RPC `ping`) e o D1. O E2E e o pós-deploy dependem do `deep`.
- Secrets do GitHub: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (repo), `SMOKE_TOKEN` (por environment: PAT **só-leitura**
  daquele ambiente). Secrets dos Workers (`ALLOWED_EMAILS`, `GOOGLE_CLIENT_SECRET`) ficam na Cloudflare, nunca no git.
- Repo público: ative `git config core.hooksPath .githooks` (o pre-commit bloqueia `.env`, `.dev.vars`, CSV, `import-out/` e tokens).
- Arquivos de texto em LF (`.gitattributes`). No Windows, editores/scripts que gravam CRLF quebram os `.sh` no runner Linux.

## Mapa (`mcp/src/`)

- `entry.py`: `WorkerEntrypoint`: `fetch` (ASGI → MCP), `queue`, `scheduled`. GET/DELETE `/mcp` → 405 (stateless).
- `limafinance/server.py`: `MCPServer` (SDK `mcp` 2.x) + registro das 13 tools + `guarded()` (erros e escopo).
- `limafinance/auth.py`: `LimaTokenVerifier` (PAT via D1, OAuth via RPC) e `current_actor()`.
- `limafinance/schemas.py`: tipos Pydantic dos parâmetros (as descrições viram o JSON Schema que o LLM lê).
- `limafinance/domain/`: regras de negócio + SQL. **Sem dependência de MCP**: recebe `Actor` + valores validados.
- `limafinance/lib/`: funções puras (dinheiro, datas, texto, ids). Rodam em CPython e Pyodide, testadas com pytest.
- `limafinance/db.py`: wrapper fino do D1 (`all`, `first`, `run`, `batch`, `batch_all`).
- `limafinance/jobs.py`: jobs da Queue (`type` + `handle_job`).
- `scripts/`: rodam em CPython (`uv run`), nunca no Worker: import da planilha, PAT, smoke.

## Regras invioláveis

1. **Dinheiro é centavo inteiro** (`amount_cents > 0`). O sinal vem de `kind` (`income`|`expense`).
   Entrada do LLM passa por `parse_amount_to_cents` (aceita "1.234,56", "12.50", número). Nunca `float("...")` em string de dinheiro.
   Saída para humanos via `format_brl`.
2. **"Hoje" vem de `today_in(actor.timezone)`** (padrão `America/Sao_Paulo`, via `zoneinfo` + `tzdata`).
   O Worker roda em UTC: `date.today()` está ERRADO entre 21h e 0h em SP.
3. **Datas são strings ISO**: dia `YYYY-MM-DD`, competência `YYYY-MM`. Competência ≠ data real.
4. **Toda query filtra por `user_id`** do `Actor`. O `user_id` vem só do token (`current_actor()`), nunca de parâmetro de tool.
5. **Categoria inexistente = erro** (`category_not_found` com sugestões). Só `create_category` cria.
6. **Sem SQL livre**. Cada operação é uma tool estreita com tipos Pydantic. Nada de tool "execute_sql".
7. **Escrita segura**: soft delete (`deleted_at`), `idempotency_key` única por usuário, lote atômico via `db.batch([...])`
   (D1 batch = transação), e `audit_stmt(...)` **no mesmo batch** de toda escrita.
8. **Valide tudo antes de escrever**: em lote, um item inválido aborta o lote inteiro, e o erro cita o item.
9. **Jobs da Queue são idempotentes** (at-least-once). Recorrências usam a chave `rec:<id>:<YYYY-MM>`.
10. **Segredos**: nunca logar tokens. O PAT só existe como SHA-256. O verificador de token **falha fechado** (erro = não autenticado).

## Convenções de tools MCP

- Nome `snake_case` com verbo: `list_*`, `add_*`, `update_*`, `delete_*`, `set_*`, `create_*`, `end_*`.
- Sempre `@mcp.tool(annotations=READ)` ou `_write(idempotent=..., destructive=...)` **e** `@guarded()` / `@guarded(write=True)`
  (nessa ordem: `mcp.tool` por fora). `guarded(write=True)` exige o escopo `mcp:write`.
- Docstring da função = descrição da tool, em **pt-BR**, curta e com exemplo. Parâmetros com `Annotated[..., Field(description=...)]`;
  reutilize os tipos de `schemas.py` (`AmountT`, `CategoryT`, `DateT`, `MonthT`...).
- Erros de domínio: `raise DomainError(code, mensagem_pt_br, details)`. `guarded` converte em `isError` com JSON `{error, message, ...}`.
  Exceção inesperada vira "Erro interno" + traceback no log.
- Resposta: dict com centavos **e** string formatada (`valor`, `gasto`, `restante`...). O LLM não deve fazer conta.
- Tool nova: lógica em `domain/`, tipo em `schemas.py`, registro em `server.py`, teste da parte pura em `tests/`.
  Mantenha o total baixo (hoje são 13): prefira parâmetro a tool nova.

## Python Workers: armadilhas

- Dependências entram via `pyproject.toml` + `uv run pywrangler sync` (vai para `python_modules/`). Só pacotes puros ou
  compilados para Pyodide. Teste o import com `pywrangler dev` antes de depender de uma lib nova.
- `mcp` é a v2: `from mcp.server.mcpserver import MCPServer` (FastMCP mudou de nome). `ToolAnnotations` usa snake_case
  nos atributos (`read_only_hint`), mas aceita o dict camelCase que passamos.
- O SDK `workers` converte bindings: `None` → `null`, resultados JS → `dict`/`list`. Não passe `bool` para o D1 (use 0/1).
- `streamable_http_app(host=...)` liga a proteção contra DNS rebinding. Os hosts permitidos vêm de `MCP_RESOURCE` (`server.http_app`).
  Com domínio próprio, basta trocar `MCP_RESOURCE`, mas confira WAF/Bot Fight Mode (bloqueiam Claude/ChatGPT).
- Cold start com pydantic é ~1 s em produção (snapshot). Evite imports pesados no topo de `entry.py`.

## Banco (D1/SQLite)

- IDs `new_id("txn")` → `txn_` + 22 chars base32 ordenáveis por tempo. Prefixos: usr, tok, cat, txn, rec, bud.
- Unicidade com NULL: colunas-chave (`parent_key`, `category_key`) em vez de índice com `COALESCE`, por causa do `ON CONFLICT`.
- Categoria: no máximo 2 níveis, comparada por `name_key = normalize_key(name)`. Raiz em filtro/resumo inclui as subcategorias.
- Migrations só para frente: crie `migrations/000N_*.sql`; nunca edite uma já aplicada.
- Nunca rode SQL de escrita `--remote` à mão sem pedir ao usuário.

## Auth

- Escopos: `mcp:read` (obrigatório), `mcp:write`, `offline_access`. Allowlist: `ALLOWED_EMAILS`, secret do Worker de auth
  (local: `auth/.dev.vars`; remoto: `wrangler secret put ALLOWED_EMAILS --env <env>`). Nunca coloque e-mails em arquivo versionado.
- Login: Google OIDC. Login dev: só com `DEV_LOGIN=true` **e** host loopback.
- O grant guarda só `{email, name}`. O Python cria/acha o usuário pelo e-mail em cada validação (`upsert_user`).
- `MCP_RESOURCE` tem que ser idêntico nos dois Workers e igual à URL que os clientes usam (os tokens ficam presos a ela).

## Estilo

- Python 3.12+, type hints, funções `async` em `domain/`. Sem ORM e sem dependência nova sem motivo (vai no bundle do Worker).
- Código e identificadores em inglês; textos para usuário/LLM em pt-BR. Comentários explicam o porquê.
