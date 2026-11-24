# Segurança

O limafinance guarda dados financeiros pessoais. Cada pessoa faz o deploy na própria conta Cloudflare, então
os dados de uma instância nunca passam por este repositório nem pelo autor.

## Modelo

- **Ninguém entra sem autenticação.** `/mcp` exige OAuth 2.1 (login Google + allowlist `ALLOWED_EMAILS`) ou um
  personal access token `lf_pat_…`, guardado só como SHA-256 e com escopo só-leitura opcional.
- **Isolamento.** O Worker de auth (TypeScript) só conhece identidade. O Worker MCP (Python) valida tokens por RPC
  via Service Binding (sem passar pela internet) e filtra toda query pelo `user_id` vindo do token, nunca de parâmetro de tool.
- **Escrita segura.** Sem SQL livre, tools estreitas com schema, soft delete, idempotência, lotes atômicos e
  `audit_log` de toda escrita.
- **Segredos** (`GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`) ficam nos secrets da Cloudflare. Nada de segredo no git:
  `.gitignore` + hook `.githooks/pre-commit` (`git config core.hooksPath .githooks`).
- **Login de desenvolvimento** (sem senha) só funciona com `DEV_LOGIN=true` **e** host loopback.

## Reportar uma vulnerabilidade

Não abra issue pública. Use **Security → Report a vulnerability** (GitHub private vulnerability reporting)
neste repositório, com passos para reproduzir. Resposta em até 7 dias.
