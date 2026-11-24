# Arquitetura do limafinance (v1, Python, para aprovação)

## Visão geral

```
 Claude (web/desktop/Code) ─┐                        ┌─────────────────────────────────────────────┐
 ChatGPT (dev mode)        ─┼── HTTPS /mcp ─────────▶│ Worker "limafinance" (PYTHON)               │
 Gemini CLI                ─┘                        │  /mcp  MCPServer (SDK mcp 2.x) stateless     │
        │                                            │        13 tools · Pydantic · domínio · SQL   │
        │ 401 → /.well-known/oauth-protected-resource│  auth: PAT "lf_pat_…" (D1)                   │
        │      aponta para o authorization server    │        ou OAuth ──RPC──┐                     │
        ▼                                            │  cron 03:00 UTC ─▶ Queue ─▶ consumer         │
 ┌───────────────────────────────────────┐           └───────────┬──────────┼──────────────────────┘
 │ Worker "limafinance-auth" (TS, ~250 l)│◀── Service Binding ───┘          │ D1 "DB" · Queue "JOBS"
 │  /authorize (consentimento)           │    validateToken(resource, token)
 │  /oauth/token|register, metadata      │
 │  login Google (allowlist) | dev local │── KV "OAUTH_KV" (grants/tokens com hash)
 └───────────────────────────────────────┘
```

## Decisões

| # | Decisão | Por quê |
|---|---|---|
| 1 | **Python para tudo que é do domínio** | Tools, regras, SQL, Queue, cron, import e testes estão em Python (Pyodide). Protótipo validado: `mcp` 2.x + pydantic rodam no Pyodide 3.14. |
| 2 | **OAuth num Worker TS separado** | `workers-oauth-provider` (DCR, CIMD, PKCE, rotação de refresh, consentimento anti-CSRF) não tem equivalente em Python. Reescrever OAuth à mão para dados financeiros seria o maior risco do projeto. O TS fica isolado, pequeno e sem acesso aos dados. |
| 3 | **Validação por RPC (Service Binding)** | O Python chama `AUTH.validateToken(resource, token)`: sem tráfego pela internet nem segredo compartilhado. É a forma "split" recomendada pela própria lib. |
| 4 | **MCP stateless** | Uma instância de `MCPServer` por requisição, `json_response`, GET/DELETE `/mcp` → 405 (não há stream nem sessão). |
| 5 | **OAuth com DCR + CIMD** | ChatGPT e Gemini CLI usam DCR. Claude usa CIMD. Ambos ligados. |
| 6 | **Login upstream = Google** | Família e amigos já têm conta Google. Allowlist por e-mail verificado. |
| 7 | **PAT para bearer fixo** | Claude Code e Gemini CLI aceitam header. O token é guardado como SHA-256 e validado no Python. Escopo só-leitura opcional. |
| 8 | **Multi-tenant desde o dia 1** | `user_id` em todas as tabelas. Vem do token, nunca de parâmetro de tool. O auth só conhece o e-mail, e o Python cria/acha o usuário. |
| 9 | **Livro-razão único** | Entrada e saída na mesma tabela, `kind` + `amount_cents > 0`. Competência separada da data real. |
| 10 | **Recorrência = regra materializada** | Gera lançamentos reais com a chave `rec:<id>:<mês>`. Resumo simples, cada lançamento editável isoladamente. |
| 11 | **Queue já na v1** | O cron faz fan-out de um job por usuário. Pronto para N usuários. Jobs novos entram em `jobs.py`. |
| 12 | **Escrita segura** | Soft delete, idempotency_key, lote atômico, validação total antes de gravar, `audit_log` no mesmo batch, escopo `mcp:write`. |
| 13 | **Import por script** | `mcp/scripts/import_planilha.py` (CPython, reaproveita `limafinance.lib`): CSV pt-BR → SQL idempotente, dry-run por padrão. |
| 14 | **Config por deploy, fora do código** | Repositório público e "deploy your own": `wrangler.base.jsonc` versionado + `limafinance.env` (ou Variables do GitHub) → `ci/render_config.py` gera os `wrangler.jsonc`. Nomes derivam de um prefixo; IDs são preenchidos por `ci/deploy_your_own.py`. |

## CI/CD

```
PR ──▶ test ─┐
       e2e  ─┼─▶ (merge na main) ─▶ staging: migrations → auth → mcp → verificação ─▶ production: idem ─▶ fim
       bundle┘                                     │ falhou                              │ falhou
                                                   └─▶ rollback (código) e para           └─▶ rollback (código)
```

- GitHub Actions. Dois ambientes Cloudflare com recursos separados (Workers, D1, KV, Queues). Produção automática no merge,
  só depois de staging passar na verificação pós-deploy.
- O e2e do CI sobe os dois Workers de verdade (wrangler dev) e faz OAuth completo + MCP + cron/Queue. A verificação
  pós-deploy é o mesmo script rodado contra a URL publicada, sem escrever dados (PAT só-leitura opcional).
- Rollback volta só o código. Por isso as migrations seguem expand/contract (ver `CLAUDE.md`).

## Custos de ter escolhido Python

- **Cold start** ~1 s em produção (snapshot de memória). Imperceptível numa conversa, mas maior que no TS.
- **Dois deploys** (auth e mcp), e o auth primeiro na primeira vez.
- **Pacotes**: só puros ou compilados para Pyodide. Toda dependência nova precisa ser testada com `pywrangler dev`.
- **Cron local**: Python Worker não tem `/__scheduled`. Usa-se a API local do wrangler (ver `CLAUDE.md`).

## Tools (13)

| Tool | Tipo | Notas |
|---|---|---|
| `get_context` | leitura | hoje (SP), mês atual, árvore de categorias. O LLM chama primeiro. |
| `list_categories` | leitura | |
| `list_transactions` | leitura | filtros: competência, intervalo de datas, kind, categoria (inclui subs), texto, excluídos; paginação; totais |
| `month_summary` | leitura | entradas, saídas, saldo, por categoria vs. meta, teto global, R$/dia restante |
| `list_recurrences` | leitura | |
| `add_transactions` | escrita | lote 1–100, atômico, idempotency_key por item |
| `update_transaction` | escrita | patch parcial |
| `delete_transactions` / `restore_transactions` | escrita | soft delete reversível (`destructiveHint` na exclusão) |
| `create_category` | escrita | único caminho para criar categoria |
| `set_budget` | escrita | upsert/remove. Sem categoria = teto global |
| `create_recurrence` / `end_recurrence` | escrita | gera o mês corrente na hora, sem backfill |

## Mapeamento da planilha

| Coluna | Destino |
|---|---|
| Mês-Competência | `transactions.competence_month` |
| Data | `transactions.occurred_on` (vazia → dia 1 da competência) |
| Categoria / Subcategoria | `categories` (2 níveis), criadas pelo import |
| Descrição, Nota | `description`, `note` |
| Valor | `amount_cents`. `kind` = `income` se a categoria estiver em `--income-categories`, senão `expense` |
| Ocorrência = Mensal | importado como lançamento normal. O relatório lista candidatos a `create_recurrence` |
| Provento | **um valor por mês** → 1 lançamento `income` "Provento do mês" (categoria `Proventos`) |
| Meta Gasto | **um valor por mês** → `budgets` global do mês |

## Pontos que ainda precisam da sua decisão

1. **Provento/Meta Gasto** são valores mensais repetidos nas linhas? (assumido; erro se divergirem no mesmo mês)
2. **Valores negativos** na planilha: hoje geram aviso e entram como positivos. "Negativo = entrada"?
3. **Google como login** (alternativas: GitHub, magic link).
4. **Escrita no ChatGPT**: só testando no seu plano.

## Fora da v1 (já previsto no desenho)

- Webapp de cadastro: a tabela `users` já existe, e a allowlist vira status no banco (o auth consultaria via RPC).
- Revogar PAT por tool/web, renomear/arquivar categoria, alertas de meta via Queue.
