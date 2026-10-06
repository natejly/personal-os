"""Auto model choice: a short plain follow-up goes to the fast model, anything heavier to the default one.

Pure and cheap on purpose: it reads the message, not the model's mind. Wrong guesses cost a slower or a
weaker answer, and the user can always pick a model by hand (an explicit pick is never routed)."""
from __future__ import annotations

import re
from typing import Any

AUTO = "auto"
SHORT = 240
_HEAVY = re.compile(r"\b(plan|analy[sz]e|analysis|compare|why|prove|debug|write a|research)\b", re.I)


def route(text: str, *, attachments: bool = False, prior_tools: bool = False, effort: str = "default",
          plan_mode: bool = False, fast_model: str = "", default_model: str = "") -> tuple[str, str]:
    """(model, reason) for one turn. Falls back to the default model when no fast model is configured."""
    if not fast_model:
        return default_model, "no fast model"
    text = (text or "").strip()
    if effort in ("high", "xhigh", "max"):
        return default_model, "high reasoning effort"
    if plan_mode:
        return default_model, "plan mode"
    if text.startswith("/"):
        return default_model, "slash command"
    if attachments:
        return default_model, "attachments"
    if "```" in text:
        return default_model, "contains code"
    if _HEAVY.search(text):
        return default_model, "asks for analysis"
    if len(text) >= SHORT:
        return default_model, "long message"
    if prior_tools:
        return default_model, "follows tool use"
    return fast_model, "short follow-up"


def wanted(model: str, explicit: str, cfg: dict[str, Any]) -> bool:
    """Auto is on when the chat's model is `auto`, or the global toggle is on and nobody picked a model."""
    return model == AUTO or bool(cfg.get("autoRoute") and not explicit and model == cfg.get("defaultModel"))


def concrete(model: str | None, cfg: dict[str, Any]) -> str:
    """A model id a background call can send: `auto` is not one, so it stands for the default model."""
    return str(cfg.get("defaultModel") or "") if not model or model == AUTO else model
