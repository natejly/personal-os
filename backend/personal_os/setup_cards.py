"""Setup cards: what the assistant shows when a feature the user asked about is not configured yet.

A card is only data ({feature, title, why, action, ...}); the chat renders it with a button. The button is the
user's click: nothing here starts a sign-in or changes a setting. `status` is the one place that knows how
to tell whether each target is already set up.
"""
from __future__ import annotations

from typing import Any, Callable

from . import firecrawl

# feature -> (title, default reason, action, where). action "google" starts the sign-in, "settings" opens a Settings tab, "view" opens a view.
TARGETS: dict[str, tuple[str, str, str, str]] = {
    "google": ("Connect Google", "Calendar, Gmail, Drive and Tasks need a Google account.", "google", "integrations"),
    "model_key": ("Add a model API key", "No model provider key is saved, so I cannot answer.", "settings", "model"),
    "telegram": ("Set up Telegram", "Texting with me from your phone needs a bot token and pairing.", "settings", "integrations"),
    "web_search": ("Add a web search key", "A search provider key gives better and fuller results.", "settings", "integrations"),
    "mcp": ("Add a connector", "Connectors give me tools for other apps and services.", "settings", "integrations"),
    "health": ("Set up health sync", "Health data needs a connected source.", "view", "health"),
    "macos": ("Grant macOS access", "Screen Recording and Accessibility let me see and drive your Mac.", "settings", "system"),
}
FEATURES = list(TARGETS)


def _macos() -> bool:
    from . import macos, osplat
    return osplat.has("screen_recording") and macos.screen_recording_status() == macos.GRANTED


def status(tb: Any) -> dict[str, bool]:
    """feature -> is it set up. A probe that fails counts as not set up."""
    cfg = tb.settings()
    probes: dict[str, Callable[[], bool]] = {
        "google": lambda: (lambda s: bool(s["connected"]) and not s["needs_reauth"])(tb.google.status()),
        "model_key": lambda: bool(cfg.get("apiKey")),
        "web_search": lambda: bool(firecrawl.key(cfg) or cfg.get("braveApiKey") or cfg.get("tavilyApiKey") or cfg.get("exaApiKey")),
        "macos": _macos,
        **getattr(tb, "setup_probes", {}),  # telegram, mcp, health: wired in app.py, which owns those stores
    }
    out: dict[str, bool] = {}
    for f in FEATURES:
        try:
            out[f] = bool(probes[f]()) if f in probes else False
        except Exception:  # noqa: BLE001
            out[f] = False
    return out


def card(feature: str, reason: str = "", *, reauth: str = "") -> dict[str, Any]:
    title, why, action, where = TARGETS[feature]
    c: dict[str, Any] = {"feature": feature, "title": title, "why": (reason or why)[:200], "action": action}
    c["view" if action == "view" else "tab"] = where
    if reauth:
        c.update(title="Reconnect Google", why=reauth[:200], reauth=True)
    return c


def google_card(tb: Any) -> dict[str, Any]:
    """The card for a Google tool that raised GoogleNotConnected: connect, or reconnect when the saved sign-in died."""
    try:
        st = tb.google.status()
    except Exception:  # noqa: BLE001
        st = {}
    reason = str(st.get("reauth_reason") or "") if st.get("connected") else ""
    return card("google", reauth=reason)
