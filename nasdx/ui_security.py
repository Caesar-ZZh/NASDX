"""Shared output-encoding helpers for Streamlit HTML boundaries."""
from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse


CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def escape_html(value: Any) -> str:
    """Escape untrusted text before interpolation into unsafe Streamlit HTML."""
    return html.escape(str(value), quote=True)


def safe_http_url(value: Any) -> str | None:
    """Return an absolute HTTP(S) URL, rejecting controls and ambiguous schemes."""
    text = str(value)
    if not text or text != text.strip() or CONTROL_RE.search(text):
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return text


def safe_external_link(label: Any, url: Any, *, title: Any = "") -> str:
    """Render a safe external link or non-clickable escaped label."""
    safe_label = escape_html(label)
    safe_url = safe_http_url(url)
    if safe_url is None:
        return safe_label
    safe_title = escape_html(title)
    return (
        f'<a href="{escape_html(safe_url)}" target="_blank" rel="noopener noreferrer" '
        f'title="{safe_title}" style="color:#9cdcfe;text-decoration:none">{safe_label}</a>'
    )
