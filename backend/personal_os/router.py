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
          plan_mode: bool = False, fast_model: str = "", default_model: str = "") -> tuple[str, str, bool]:
    """(model, reason, no_think) for one turn. A short follow-up goes to the fast model; with none distinct from the
    default model it stays on the default one with the thinking pass off. Only a raised effort (xhigh, max) blocks it."""
    text = (text or "").strip()
    if effort in ("xhigh", "max"):
        return default_model, "high reasoning effort", False
    if plan_mode:
        return default_model, "plan mode", False
    if text.startswith("/"):
        return default_model, "slash command", False
    if attachments:
        return default_model, "attachments", False
    if "```" in text:
        return default_model, "contains code", False
    if _HEAVY.search(text):
        return default_model, "asks for analysis", False
    if len(text) >= SHORT:
        return default_model, "long message", False
    if prior_tools:
        return default_model, "follows tool use", False
    if not fast_model or fast_model == default_model:
        return default_model, "short follow-up, thinking off", True
    return fast_model, "short follow-up", False


def wanted(model: str, explicit: str, cfg: dict[str, Any]) -> bool:
    """Auto is on when the chat's model is `auto`, or the global toggle is on and nobody picked a model."""
    return model == AUTO or bool(cfg.get("autoRoute") and not explicit and model == cfg.get("defaultModel"))


def concrete(model: str | None, cfg: dict[str, Any]) -> str:
    """A model id a background call can send: `auto` is not one, so it stands for the default model."""
    return str(cfg.get("defaultModel") or "") if not model or model == AUTO else model
