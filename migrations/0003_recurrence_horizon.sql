-- Recorrências geradas com até 12 meses de antecedência (horizonte rolante).
-- recurrence_override = 1: o lançamento gerado foi editado à mão (rateio, valor pontual...). O reajuste da regra
-- (update_recurrence) e o encerramento (end_recurrence) NÃO mexem nele.
-- Aditiva (expand/contract): coluna com default + índice novo; o código anterior continua funcionando.

ALTER TABLE transactions ADD COLUMN recurrence_override INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_transactions_recurrence ON transactions (user_id, recurrence_id, competence_month)
  WHERE recurrence_id IS NOT NULL;
