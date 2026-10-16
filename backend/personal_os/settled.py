"""What a finished reply sets going once it is out: the built-in `turn.settled` subscribers (design doc 5.6).

`_chat_stream` emits one Settled record after the reply is stored and `done` is out; writing-style learning, auto-learn,
follow-up chips and the model title each decide from it whether they apply. They run as the reply's tail, so the chat
stays busy (a second message is a 409) until they return, and anything slow goes to a worker from here.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

from . import learn, titles
from .extensions.hooks import HookBus
from .learn import LearnJob
from .style import learn_style_from_exchange, looks_like_prose
from .tools import scrub_strings
from .trace import Tracer


@dataclass
class SettledView:
    """What an extension subscriber receives: copies with secrets out of the settings and credentials scrubbed from every string."""
    conversation_id: str
    message_id: str  # the reply's row
    user_message_id: str | None
    conv: dict[str, Any]  # the conversation as this turn loaded it
    cfg: dict[str, Any]
    model: str
    user_text: str
    run_user_texts: list[str]  # every user message this run answered (a steer adds one)
    reply: str
    error: str | None
    stopped: bool
    gone: bool  # the chat was deleted mid-reply: the exchange is the user's to discard
    internal: bool  # the turn's "user" text is the backend's own (a wake report, a nudge)
    in_side: bool  # one side of a model duel
    proposal_only: bool  # an unattended run that may only propose
    desk_id: str | None
    tainted: bool
    tool_events: list[dict[str, Any]]
    used: dict[str, Any]  # what went into the context, for the skills the reply followed
    placeholder_title: str | None  # the instant title this turn wrote, to be replaced by a model title


@dataclass
class Settled(SettledView):
    """The built-in subscribers' record: the live objects they extend and the full settings."""
    tracer: Tracer
    publish: Callable[[str, Any], None]  # the run's event stream


def view_of(s: Settled, public_cfg: Callable[[], dict[str, Any]]) -> SettledView:
    shared = {f.name: scrub_strings(getattr(s, f.name)) for f in fields(SettledView) if f.name != "cfg"}
    return SettledView(cfg=public_cfg(), **shared)


def register(bus: HookBus, *, style: Any, relearn: Callable[[str | None, str], None], learner: Any, skills: Any,
             followup_jobs: Any, title_jobs: Any, set_trace: Callable[[str, list[dict[str, Any]]], None],
             get_conversation: Callable[..., dict[str, Any] | None], said: Callable[[dict[str, Any]], bool]) -> None:
    async def learn_style(s: Settled) -> None:
        # From the user's half of the exchange only. Banking a sample is free and never calls the model; the re-read
        # goes to the learn worker. The prose check runs before the span, so an ordinary short instruction leaves no
        # trace of a step that did nothing. A tainted chat may still bank the user's own prose: looks_like_prose
        # already rejects pastes and quotes.
        if (s.error or s.gone or s.internal or s.in_side or not s.cfg.get("learnStyle", True)
                or not s.conv["settings"].get("autoLearn", True) or not looks_like_prose(s.user_text)):
            return
        span = s.tracer.start("style", "Learn writing style")
        s.publish("span", {"message_id": s.message_id, "span": span})
        try:
            banked = await learn_style_from_exchange(settings=s.cfg, style=style, project_id=s.conv["project_id"],
                                                     user_text=s.user_text, model=s.model)
            s.tracer.end(span, {"sample_chars": (banked or {}).get("sample", {}).get("chars", 0)})
            s.publish("span", {"message_id": s.message_id, "span": span})
            if banked:
                relearn(s.conv["project_id"], s.model)
                # The banked-sample notice only; a refreshed profile arrives on /events.
                s.publish("style_learned", {"project_id": s.conv["project_id"], "profile": None, "sample_id": banked["sample"]["id"]})
        except Exception as e:  # noqa: BLE001 - as quiet as a failed memory extraction
            s.tracer.end(span, error=str(e))
            s.publish("span", {"message_id": s.message_id, "span": span})
        set_trace(s.message_id, s.tracer.spans)

    async def auto_learn(s: Settled) -> None:
        # Another LLM call, so the exchange goes to the worker and what it learns arrives on the app topic. A scheduled
        # run never writes to long-term memory: what it found belongs in its report and the inbox. A tainted reply has
        # read someone else's text, so only the user's own words this run are mined (the reply, its tool calls and the
        # procedures it followed could plant that text in later chats).
        if (s.error or not s.reply or s.gone or s.proposal_only or s.internal or s.in_side
                or (s.tainted and not any(t.strip() for t in s.run_user_texts))  # an attachment-only message has no words to mine
                or not s.cfg.get("autoLearn", True) or not s.conv["settings"].get("autoLearn", True)
                or not s.conv["settings"].get("useMemory", True)):  # memory off: nothing written for other chats to read
            return
        user_only = s.tainted
        learner.submit(LearnJob(
            conversation_id=s.conversation_id, message_id=s.message_id, project_id=s.conv["project_id"],
            user_text="\n\n".join(s.run_user_texts) if user_only else s.user_text,
            assistant_text="" if user_only else s.reply, model=s.model, settings=s.cfg,
            spans=list(s.tracer.spans), tool_events=[] if user_only else list(s.tool_events),
            skills_in_use=[] if user_only else learn.skills_seen(s.used["skills"], s.tool_events, skills, s.conv["project_id"]),
            user_only=user_only, user_message_id=s.user_message_id, learn_graph=bool(s.conv["settings"].get("useGraph", True)),
        ))

    async def follow_ups(s: Settled) -> None:
        # Chips after a finished reply only: not an error, a Stop, or an unattended run.
        if (not s.error and s.reply and not s.gone and not s.stopped and not s.proposal_only and not s.desk_id
                and not s.internal and not s.in_side and s.cfg.get("followUps", True)):
            followup_jobs.spawn(s.conversation_id, s.message_id, s.conv["project_id"], s.user_text, s.reply, s.model, s.cfg)

    async def title(s: Settled) -> None:
        # From the user's typed text only, so no taint or autoLearn gate. It replaces the placeholder this turn wrote, or
        # (once, at RETITLE_AT user turns) an earlier auto title; a title the user typed is never touched.
        if s.error or s.gone or s.internal or s.in_side or s.proposal_only or not s.cfg.get("autoTitle", True):
            return
        user_texts = [m["content"] for m in s.conv["messages"] if said(m)] + [s.user_text]
        fresh = get_conversation(s.conversation_id, with_messages=False)
        fs = (fresh or {}).get("settings") or {}
        if s.placeholder_title is not None:
            if fs.get("titleSource") != "user" and fresh and fresh["title"] == s.placeholder_title:
                title_jobs.spawn(s.conversation_id, s.conv["project_id"], s.placeholder_title, user_texts, s.model, s.cfg, len(user_texts))
        elif (fresh and fs.get("titleSource") == "auto" and len(user_texts) >= titles.RETITLE_AT
              and int(fs.get("titleTurns") or 0) < titles.RETITLE_AT):
            title_jobs.spawn(s.conversation_id, s.conv["project_id"], fresh["title"], user_texts, s.model, s.cfg, len(user_texts))
        elif fresh and not fs.get("titleSource") and fresh["title"] == titles.placeholder(user_texts[0]):
            # The first title job failed (model error, shutdown, an errored first turn): retry on a later turn.
            title_jobs.spawn(s.conversation_id, s.conv["project_id"], fresh["title"], user_texts, s.model, s.cfg, len(user_texts))

    # Style first: auto-learn copies the trace, which then has to hold the style span. Handlers that never suspend
    # finish in the order they were subscribed.
    bus.subscribe("turn.settled", learn_style, owner="core", order=0)
    bus.subscribe("turn.settled", auto_learn, owner="core", order=0)
    bus.subscribe("turn.settled", follow_ups, owner="core", order=0)
    bus.subscribe("turn.settled", title, owner="core", order=0)
