"""Auto model choice: a short plain follow-up goes to the fast model, anything heavier to the default one.

Pure and cheap on purpose: it reads the message, not the model's mind. Wrong guesses cost a slower or a
weaker answer, and the user can always pick a model by hand (an explicit pick is never routed)."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from .runtime.contracts import ROUTER_BUDGET_S, route_problem
from .runtime.protocols import RouteChoice
from .runtime.slots import Runtime

AUTO = "auto"
SHORT = 240
MUTE_AFTER = 5  # consecutive failures of a selected router before it is skipped for the session
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


class Heuristic:
    """core/heuristic: `route` behind the model.router interface."""
    id = "core/heuristic"

    def route(self, turn: dict[str, Any]) -> RouteChoice:
        model, reason, no_think = route(
            turn["text"], attachments=turn["attachments"], prior_tools=turn["prior_tools"], effort=turn["effort"],
            plan_mode=turn["plan_mode"], fast_model=turn["fast_model"], default_model=turn["default_model"])
        return {"model": model, "effort": "none" if no_think else turn["effort"], "reason": reason, "no_think": no_think}


HEURISTIC = Heuristic()


async def choose(rt: Runtime, turn: dict[str, Any], served: Callable[[str], bool]) -> tuple[RouteChoice, str | None]:
    """The route for one turn, and a notice for the user when the selected router could not give it. A router that raises, is
    slower than its budget or names a model no configured provider serves is replaced by the default's answer for the turn,
    and after MUTE_AFTER failures in a row it is skipped for the session."""
    default = HEURISTIC.route(turn)
    chosen = rt.alternative("model.router")
    if chosen is None or rt.router_failures.get(chosen.id, 0) >= MUTE_AFTER:
        return default, None
    try:
        got = await asyncio.wait_for(asyncio.to_thread(chosen.impl.route, turn), ROUTER_BUDGET_S)
        if problem := route_problem(got) or (None if served(got["model"]) else f"model {got['model']!r} is not served by any configured provider"):
            raise ValueError(problem)
    except Exception as e:  # noqa: BLE001 - whatever a router does wrong, the turn still gets a route
        rt.router_failures[chosen.id] = rt.router_failures.get(chosen.id, 0) + 1
        why = f"{chosen.label} could not route this turn ({str(e) or type(e).__name__}); the default router was used."
        rt.announce("model.router", chosen.id, "core/heuristic", why, False)
        return default, why
    rt.router_failures[chosen.id] = 0
    return got, None
