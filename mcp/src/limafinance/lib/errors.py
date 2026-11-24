from typing import Any


class DomainError(Exception):
    """Erro de domínio: mensagem em pt-BR pensada para o LLM corrigir a chamada e tentar de novo.

    As tools convertem em resultado MCP `isError: true` (não em erro de protocolo).
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
