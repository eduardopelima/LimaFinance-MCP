import hashlib
import secrets
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, minúsculo


def new_id(prefix: str) -> str:
    """ID ordenável por tempo: `txn_` + 10 chars de timestamp(ms) + 12 aleatórios. Prefixos: usr tok cat txn rec bud."""
    t = int(time.time() * 1000)
    head = ""
    for _ in range(10):
        head = _ALPHABET[t % 32] + head
        t //= 32
    tail = "".join(_ALPHABET[b % 32] for b in secrets.token_bytes(12))
    return f"{prefix}_{head}{tail}"


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()
