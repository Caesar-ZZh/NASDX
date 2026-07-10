from __future__ import annotations

from types import ModuleType
from typing import Callable


DEFAULT_TITLE = "NASDX Desktop"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 860


def load_webview() -> ModuleType | None:
    try:
        import webview
    except Exception:
        return None
    return webview


def open_webview(
    url: str,
    *,
    title: str = DEFAULT_TITLE,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    webview_module: ModuleType | None = None,
    on_closed: Callable[[], None] | None = None,
) -> bool:
    """Open a blocking pywebview window when available.

    Returns False when pywebview or WebView2 is unavailable so callers can use
    the browser fallback without making pywebview a required dependency.
    """
    webview = webview_module or load_webview()
    if webview is None:
        return False

    try:
        webview.create_window(title, url, width=width, height=height)
        webview.start()
    except Exception:
        return False

    if on_closed is not None:
        on_closed()

    return True
