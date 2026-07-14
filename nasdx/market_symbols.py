"""Exchange-aware symbol routing for mainland equity data providers."""
from __future__ import annotations


_PREFIX_EXCHANGES = {"sh": "SSE", "sz": "SZSE", "bj": "BSE"}
_EXCHANGE_PREFIXES = {exchange: prefix for prefix, exchange in _PREFIX_EXCHANGES.items()}


def resolve_exchange(code: str) -> str:
    """Resolve Shanghai, Shenzhen, or Beijing exchange from a stock code."""
    normalized = str(code).strip().lower()
    prefix = normalized[:2]
    if prefix in _PREFIX_EXCHANGES:
        return _PREFIX_EXCHANGES[prefix]
    if normalized.startswith(("4", "8", "920")):
        return "BSE"
    if normalized.startswith(("5", "6", "9")):
        return "SSE"
    return "SZSE"


def market_symbol(code: str) -> str:
    """Return the Tencent-style exchange-qualified symbol."""
    normalized = str(code).strip().lower()
    if normalized[:2] in _PREFIX_EXCHANGES:
        return normalized
    exchange = resolve_exchange(normalized)
    return f"{_EXCHANGE_PREFIXES[exchange]}{normalized}"
