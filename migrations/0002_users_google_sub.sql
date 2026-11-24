-- Identidade estável do Google: o claim `sub` nunca muda (o e-mail pode mudar ou ser reaproveitado).
-- Aditiva (expand/contract): coluna nullable + índice; o código anterior continua funcionando.
-- Usuários existentes (criados por e-mail, import ou PAT) são ligados ao `sub` no primeiro login Google.

ALTER TABLE users ADD COLUMN google_sub TEXT;
CREATE UNIQUE INDEX ux_users_google_sub ON users (google_sub) WHERE google_sub IS NOT NULL;
