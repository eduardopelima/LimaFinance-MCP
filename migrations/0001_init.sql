-- limafinance — schema inicial.
-- Convenções: IDs TEXT (prefixo + aleatório), datas ISO (YYYY-MM-DD), competência YYYY-MM,
-- timestamps UTC ISO-8601, dinheiro em centavos INTEGER (> 0; o sinal vem de `kind`).
-- Toda tabela de dados tem user_id: multi-tenant desde o dia 1.

PRAGMA foreign_keys = ON;

CREATE TABLE users (
  id          TEXT PRIMARY KEY,                       -- usr_...
  email       TEXT NOT NULL UNIQUE,                   -- normalizado em minúsculas
  name        TEXT,
  timezone    TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
  currency    TEXT NOT NULL DEFAULT 'BRL',
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Personal access tokens (bearer fixo). Só o SHA-256 é guardado.
CREATE TABLE api_tokens (
  id            TEXT PRIMARY KEY,                     -- tok_...
  user_id       TEXT NOT NULL REFERENCES users(id),
  name          TEXT NOT NULL,                        -- ex.: "claude-code", "gemini-cli"
  token_hash    TEXT NOT NULL UNIQUE,                 -- hex(sha256(token))
  scopes        TEXT NOT NULL DEFAULT 'mcp:read mcp:write',
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  last_used_at  TEXT,
  expires_at    TEXT,
  revoked_at    TEXT
);

-- Categoria (parent_id NULL) ou subcategoria (parent_id = categoria). Máximo 2 níveis.
CREATE TABLE categories (
  id           TEXT PRIMARY KEY,                      -- cat_...
  user_id      TEXT NOT NULL REFERENCES users(id),
  parent_id    TEXT REFERENCES categories(id),
  parent_key   TEXT NOT NULL DEFAULT '',              -- = COALESCE(parent_id, '') p/ unicidade
  name         TEXT NOT NULL,
  name_key     TEXT NOT NULL,                         -- minúsculas, sem acento, espaços colapsados
  kind         TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  archived_at  TEXT
);
CREATE UNIQUE INDEX ux_categories_name ON categories (user_id, parent_key, name_key);

-- Regra de recorrência (Ocorrência = Mensal). Materializada em transactions pelo job da Queue.
CREATE TABLE recurrences (
  id             TEXT PRIMARY KEY,                    -- rec_...
  user_id        TEXT NOT NULL REFERENCES users(id),
  kind           TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
  amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
  category_id    TEXT NOT NULL REFERENCES categories(id),
  description    TEXT NOT NULL,
  note           TEXT,
  day_of_month   INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31), -- 31 => último dia do mês
  start_month    TEXT NOT NULL,                       -- YYYY-MM (inclusive)
  end_month      TEXT,                                -- YYYY-MM (inclusive) ou NULL = sem fim
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  deleted_at     TEXT
);
CREATE INDEX ix_recurrences_user ON recurrences (user_id) WHERE deleted_at IS NULL;

-- Livro-razão único de entradas e saídas.
CREATE TABLE transactions (
  id                TEXT PRIMARY KEY,                 -- txn_...
  user_id           TEXT NOT NULL REFERENCES users(id),
  kind              TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
  amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
  occurred_on       TEXT NOT NULL,                    -- data real, YYYY-MM-DD
  competence_month  TEXT NOT NULL,                    -- mês de competência, YYYY-MM
  category_id       TEXT NOT NULL REFERENCES categories(id),
  description       TEXT NOT NULL,
  note              TEXT,
  recurrence_id     TEXT REFERENCES recurrences(id),
  source            TEXT NOT NULL CHECK (source IN ('mcp', 'import', 'recurrence')),
  client            TEXT,                             -- quem escreveu (client_id OAuth ou nome do PAT)
  idempotency_key   TEXT,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  deleted_at        TEXT                              -- soft delete
);
-- NULLs não colidem em UNIQUE no SQLite: chave opcional, mas única quando presente.
CREATE UNIQUE INDEX ux_transactions_idem ON transactions (user_id, idempotency_key);
CREATE INDEX ix_transactions_competence ON transactions (user_id, competence_month) WHERE deleted_at IS NULL;
CREATE INDEX ix_transactions_occurred ON transactions (user_id, occurred_on) WHERE deleted_at IS NULL;
CREATE INDEX ix_transactions_category ON transactions (user_id, category_id) WHERE deleted_at IS NULL;

-- Meta de gasto (teto) por mês: category_id NULL = teto global do mês.
CREATE TABLE budgets (
  id            TEXT PRIMARY KEY,                     -- bud_...
  user_id       TEXT NOT NULL REFERENCES users(id),
  month         TEXT NOT NULL,                        -- YYYY-MM
  category_id   TEXT REFERENCES categories(id),
  category_key  TEXT NOT NULL DEFAULT '*',            -- = COALESCE(category_id, '*') p/ unicidade
  amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX ux_budgets_scope ON budgets (user_id, month, category_key);

-- Trilha de auditoria de toda escrita (gravada no mesmo batch atômico da escrita).
CREATE TABLE audit_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      TEXT NOT NULL,
  at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  actor        TEXT NOT NULL,                         -- client_id OAuth, "pat:<nome>", "job", "import"
  action       TEXT NOT NULL,                         -- ex.: transaction.create
  entity_id    TEXT NOT NULL,
  payload      TEXT                                   -- JSON
);
CREATE INDEX ix_audit_user ON audit_log (user_id, at);
