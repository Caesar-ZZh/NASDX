"""Compatibility helpers for request routing.

Older NASDX quant modules imported this file for a global ``requests.get``
monkey patch. Production code now avoids import-time HTTP mutation; requests'
native environment handling is used instead.
"""
from __future__ import annotations

import requests


PROXIED_DOMAINS = (
    "eastmoney.com",
    "sina.com",
    "qq.com",
    "gtimg.com",
    "10jqka.com",
    "xueqiu.com",
    "jisilu.cn",
)


def configure_requests() -> dict:
    """Return the active routing policy without mutating global requests."""
    return {
        "patched": False,
        "mode": "native_requests",
        "trust_env_default": True,
        "proxied_domains": list(PROXIED_DOMAINS),
    }


def new_session(trust_env: bool = True) -> requests.Session:
    """Create a local requests session with explicit proxy-env behavior."""
    session = requests.Session()
    session.trust_env = trust_env
    return session
