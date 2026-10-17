"""The hook catalog (design doc section 5 and 10.9): one `HookSpec` per hook, and the payload TypedDicts they name.

This registry is the single source. The tables in the design doc and docs/extension-hooks.md, grain_sdk/hooks.py and
src/shared/extensionHooks.ts are generated from it (`grain ext gen-hooks`), and `grain ext check --catalog` verifies it
against every call site. To add a hook: add a payload TypedDict and a `HookSpec`, fire it from the code, run the generator.
Hook ids are never reused or retyped; retire one with `deprecated` and a CATALOG_VERSION bump.

It lives apart from hooks.py because the types it names (Block, Deny, ...) import modules that import the bus.
"""

from dataclasses import dataclass
from typing import Any, Callable, Literal, NotRequired, TypedDict

from ..context import Block
from .hooks import Ask, Cancel, Deny

CATALOG_VERSION = "1.0"


@dataclass(frozen=True)
class HookSpec:
    id: str
    kind: Literal["emit", "collect", "pipe", "gate"]
    payload: type  # a TypedDict
    section: str  # the table of the design doc it renders into: "5.5"
    fires_at: str
    order_failure: str
    since: str  # the catalog version that introduced it
    sync: bool = False  # fires through emit_sync / pipe_sync, so a handler runs on the calling thread
    returns: Any = None  # what a handler returns, for collect, pipe and gate: a TypedDict, a class or a union of them
    subscribers: Literal["any", "builtin", "grant"] = "any"  # grant: built-in, or an extension that declares `permission`
    permission: tuple[str, ...] = ()
    payload_of: Callable[[Any], dict[str, Any]] | None = None  # for a payload that is an object: the dict the strict check validates
    deprecated: str | None = None


class AppStartPayload(TypedDict):
    """Nothing: a handler starts its loops here."""


class AppStopPayload(TypedDict):
    """Nothing: a handler finishes its work here."""


class SettingsChangedPayload(TypedDict):
    """Secret keys are listed in `keys`, without values."""
    keys: list[str]
    old: dict[str, Any]
    new: dict[str, Any]


class ChatPayload(TypedDict):
    id: str
    project_id: str | None


class ProjectPayload(TypedDict):
    id: str


class ExtChangedPayload(TypedDict):
    id: str
    enabled: bool


class ContextContributePayload(TypedDict):
    """Ids only: a handler reads what it needs, and raw message text is not handed out."""
    conversation_id: str
    project_id: str | None


class ModelSelectPayload(TypedDict):
    """`reason` is the router's (empty when it did not run); `tier` is `fast` or `default`."""
    model: str
    effort: str
    reason: str
    tier: Literal["fast", "default"]


class ModelRequestPayload(TypedDict):
    """Request bodies are not handed out."""
    model: str
    message_count: int
    tool_names: list[str]
    tokens_est: int


class ModelResponsePayload(TypedDict):
    """The usage record of one model call (chat, helper, embedding, image or audio); the optional keys are set when the caller knew them."""
    model: str
    kind: str
    duration_ms: int
    estimated: bool
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    conversation_id: NotRequired[str | None]
    project_id: NotRequired[str | None]
    tag: NotRequired[str]
    round: NotRequired[int]


class ToolContext(TypedDict):
    run_id: str | None
    conversation_id: str | None
    project_id: str | None
    agent_id: str | None
    desk_id: str | None
    tainted: bool


class ToolPrePayload(TypedDict):
    name: str
    args: dict[str, Any]
    call_id: str
    ctx: ToolContext


class ToolPostPayload(TypedDict):
    """Strings are credential-scrubbed."""
    name: str
    call_id: str
    run_id: str | None
    args: dict[str, Any]
    result_preview: str
    error: str | None
    duration_ms: int


class ToolResultPayload(TypedDict):
    """The whole result, so every channel a loop later splits out of it is covered."""
    name: str
    call_id: str
    run_id: str | None
    result: Any
    tainted: bool


class ToolTaintPayload(TypedDict):
    name: str
    source: str


class ToolErrorPayload(TypedDict):
    name: str
    call_id: str
    run_id: str | None
    error: str


class ReplyUsage(TypedDict):
    tokens: int
    cost: float
    rounds: int


class ReplyTaint(TypedDict):
    tainted: bool
    sources: list[str]


class ReplyCompletePayload(TypedDict):
    """A deep copy with every string credential-scrubbed."""
    message_id: str
    text: str
    tool_events: list[dict[str, Any]]
    usage: ReplyUsage
    trace: list[dict[str, Any]]
    taint: ReplyTaint
    outcome: str | None


class TurnSettledPayload(TypedDict):
    """Delivered as an object with these attributes. An extension gets a copy with the secret settings blanked and the reply scrubbed."""
    conversation_id: str
    message_id: str
    user_message_id: str | None
    conv: dict[str, Any]
    cfg: dict[str, Any]
    model: str
    user_text: str
    run_user_texts: list[str]
    reply: str
    error: str | None
    stopped: bool
    gone: bool
    internal: bool
    in_side: bool
    proposal_only: bool
    desk_id: str | None
    tainted: bool
    tool_events: list[dict[str, Any]]
    used: dict[str, Any]
    placeholder_title: str | None


class RunAfterPayload(TypedDict):
    """Delivered as the run object, which has these attributes."""
    run_id: str
    conversation_id: str
    kind: str
    desk_id: str | None
    turn: int
    status: str
    error: str | None
    message_id: str | None
    input: dict[str, Any]
    cost: float
    rounds: int


class SubagentPayload(TypedDict):
    """`exit_reason` and `error` are empty at start; at stop they say why the child ended. `detached` is true for a background worker."""
    child_id: str
    role: str
    parent: str | None
    exit_reason: str
    error: str | None
    detached: bool


class MemoryBeforeWritePayload(TypedDict):
    content: str
    kind: str
    source: str
    project_id: str | None


class MemoryChangedPayload(TypedDict):
    """`memory` is a copy of the row."""
    memory: dict[str, Any]
    source: str


class CompactionMessage(TypedDict):
    role: str
    content: str


class CompactionBeforePayload(TypedDict):
    """A handler returns the payload with `summary` set to replace the built-in summarizer's text."""
    messages: list[CompactionMessage]
    window: int
    summary: str | None


class CompactionAfterPayload(TypedDict):
    summary: str


class ApprovalRequestedPayload(TypedDict):
    call_id: str
    tool: str
    args_digest: str
    run_id: str | None


class ApprovalResolvedPayload(TypedDict):
    call_id: str
    decision: str
    by: str | None


class JobFiredPayload(TypedDict):
    """`trigger` is the fire's own (`dir`, `mail`, `calendar`, `clock+dir`), else `manual` or `clock`."""
    job_id: str
    kind: str
    trigger: str


class JobDonePayload(TypedDict):
    """`outcome` is the run row's status."""
    job_id: str
    run_id: str
    outcome: str | None


class ChannelInboundPayload(TypedDict):
    channel: str
    sender: str
    text: str
    files: list[str]


class ChannelOutboundPayload(TypedDict):
    """A handler returns the payload with `text` and `files` changed, or Cancel."""
    channel: str
    text: str
    files: list[dict[str, Any]]


class CodingToolPrePayload(TypedDict):
    """Strings are credential-scrubbed and cut to 2 KB each, the whole to 16 KB."""
    session_id: str
    agent: str
    tool: str
    input: dict[str, Any]
    cwd: str | None
    worktree: str | None
    permission_mode: str | None
    lifted: bool
    call_id: str | None
    conversation_id: str | None
    run_id: str | None


class CodingToolPostPayload(CodingToolPrePayload):
    result_preview: NotRequired[str]
    duration_ms: NotRequired[int]


def _attrs(payload: type) -> Callable[[Any], dict[str, Any]]:
    """The payload dict of an object that carries the payload's keys as attributes."""
    return lambda obj: {key: getattr(obj, key) for key in payload.__annotations__}


_GATE = ("hooks:gate",)

HOOKS: dict[str, HookSpec] = {s.id: s for s in (
    HookSpec("app.start", "emit", AppStartPayload, "5.1", "an extension is loaded and the database and core services exist",
             "topological by `requires`; the extension's handlers run even while it is disabled", "1.0"),
    HookSpec("app.stop", "emit", AppStopPayload, "5.1", "the app shuts down, or the extension is unloaded",
             "reverse order; 5 s budget, then cancelled; run even while the extension is disabled", "1.0"),
    HookSpec("settings.changed", "emit", SettingsChangedPayload, "5.1", "after `PUT /settings` has validated and stored a patch",
             "`emit`", "1.0"),
    HookSpec("chat.created", "emit", ChatPayload, "5.1", "a conversation was created", "`emit`", "1.0"),
    HookSpec("chat.deleted", "emit", ChatPayload, "5.1", "a conversation was deleted", "`emit`", "1.0"),
    HookSpec("project.created", "emit", ProjectPayload, "5.1", "a project was created", "`emit`", "1.0"),
    HookSpec("project.deleted", "emit", ProjectPayload, "5.1", "a project was deleted", "`emit`", "1.0"),
    HookSpec("ext.changed", "emit", ExtChangedPayload, "5.1", "an extension was enabled or disabled",
             "`emit`; run even while the extension is disabled", "1.0"),
    HookSpec("context.contribute", "collect", ContextContributePayload, "5.3", "`build_context`, while the turn's prompt is assembled",
             "`collect` ordered by priority; fitted to the extension's share of the window; a failure or timeout drops the block "
             "and is listed in `context_used.skipped`; a stable block gets 100 ms", "1.0", returns=Block),
    HookSpec("model.select", "pipe", ModelSelectPayload, "5.4", "`_select_model` in the chat loop, after the router",
             "one winner chain; a model whose provider is not configured is ignored, and so is anything but a `{model, effort}` dict",
             "1.0", returns=ModelSelectPayload),
    HookSpec("model.request", "emit", ModelRequestPayload, "5.4", "`llm.stream_chat` and `llm.complete`, once per call before the first send "
             "(retries do not repeat it)", "`emit_sync`: a sync handler runs inline on the calling thread, an async one is started on the loop",
             "1.0", sync=True),
    HookSpec("model.response", "emit", ModelResponsePayload, "5.4", "whenever a model call records its usage",
             "`emit_sync`, as `model.request`; the core subscriber writes the usage row", "1.0", sync=True),
    HookSpec("tool.pre", "gate", ToolPrePayload, "5.5", "top of `dispatch_tool`, after the gate chain, before the body",
             "`gate`: `Deny` or `Ask`, never a rewrite of the arguments; fails toward ask", "1.0", returns=Deny | Ask,
             subscribers="grant", permission=_GATE),
    HookSpec("tool.post", "emit", ToolPostPayload, "5.5", "after the call returns, or fails", "`emit`", "1.0"),
    HookSpec("tool.result", "pipe", ToolResultPayload, "5.5", "after the call, before the result is rendered for the model",
             "`pipe` over the whole result, so every channel a loop later splits out of it is covered", "1.0", returns=ToolResultPayload),
    HookSpec("tool.taint", "emit", ToolTaintPayload, "5.5", "a call brought third-party text into the run", "`emit`; the taint is monotonic "
             "and cleared only by the user", "1.0"),
    HookSpec("tool.error", "emit", ToolErrorPayload, "5.5", "a call raised or returned an error", "`emit`", "1.0"),
    HookSpec("reply.complete", "emit", ReplyCompletePayload, "5.6", "after `done` is yielded, before the run's background jobs end",
             "`emit`; not fired for a silent wake turn", "1.0"),
    HookSpec("turn.settled", "emit", TurnSettledPayload, "5.6", "after `reply.complete`, on the same task",
             "`emit`; the built-in subscribers (style learning first, then auto-learn, follow-ups, title) each keep their own conditions; "
             "the chat is busy until the handlers return, so slow work goes to a worker", "1.0", payload_of=_attrs(TurnSettledPayload)),
    HookSpec("run.after", "emit", RunAfterPayload, "5.6", "`RunBus._drive`'s `finally`, before the run is closed",
             "`emit`; the after-snapshot subscriber has a 120 s budget, so a closing run waits for it", "1.0",
             payload_of=_attrs(RunAfterPayload)),
    HookSpec("subagent.start", "emit", SubagentPayload, "5.6", "a queued worker or subagent begins running", "`emit_sync`", "1.0", sync=True),
    HookSpec("subagent.stop", "emit", SubagentPayload, "5.6", "a worker or subagent finished", "`emit_sync`", "1.0", sync=True),
    HookSpec("memory.before_write", "pipe", MemoryBeforeWritePayload, "5.7", "`Memories.create`, `supersede` and `update`, before the write",
             "`pipe_sync` (sync handlers only); a denial raises `MemoryRefused` (HTTP 403 on the memory routes)", "1.0", sync=True,
             returns=MemoryBeforeWritePayload | Deny, subscribers="grant", permission=("memory:gate",)),
    HookSpec("memory.written", "emit", MemoryChangedPayload, "5.7", "`Memories.create`, `supersede` and `update`, after commit",
             "`emit_sync`", "1.0", sync=True),
    HookSpec("memory.deleted", "emit", MemoryChangedPayload, "5.7", "`Memories.delete` and `invalidate`", "`emit_sync`", "1.0", sync=True),
    HookSpec("compaction.before", "pipe", CompactionBeforePayload, "5.7", "`Compactor.compact`, before the summarizer is called",
             "one winner (the last to set `summary`); a failing or empty one changes nothing, so the built-in summarizer runs", "1.0",
             returns=CompactionBeforePayload),
    HookSpec("compaction.after", "emit", CompactionAfterPayload, "5.7", "`Compactor.compact`, after the summary is saved", "`emit`", "1.0"),
    HookSpec("approval.requested", "emit", ApprovalRequestedPayload, "5.8", "`RunStore.open_approval`, when the card is newly opened",
             "`emit_sync`; not mutable: first-decision-wins and the `args_digest` binding are security invariants", "1.0", sync=True),
    HookSpec("approval.resolved", "emit", ApprovalResolvedPayload, "5.8", "`RunStore.decide`, when this call won the decision",
             "`emit_sync`", "1.0", sync=True),
    HookSpec("job.fired", "emit", JobFiredPayload, "5.9", "`_launch_job` and `_launch_desk_job`, once the run (or desk) has started",
             "`emit`", "1.0"),
    HookSpec("job.done", "emit", JobDonePayload, "5.9", "the run task's done callback, after the row is final", "`emit`", "1.0"),
    HookSpec("channel.inbound", "emit", ChannelInboundPayload, "5.10", "a channel adapter hands the user's message to a chat",
             "`emit`; the owner check stays in the adapter", "1.0"),
    HookSpec("channel.outbound", "pipe", ChannelOutboundPayload, "5.10", "a channel adapter is about to send a reply",
             "`pipe`, chained (the last handler does not simply win)", "1.0", returns=ChannelOutboundPayload | Cancel),
    HookSpec("coding.tool.pre", "gate", CodingToolPrePayload, "5.12", "a Grain-started coding session is about to run a tool, "
             "via `POST /coding-hooks/{session}/pre`",
             "`gate`: `Deny` or `Ask`, the strictest of this and the local floor wins; 2 s bus budget, 3 s socket timeout; a failure, "
             "timeout or bad return becomes `Ask`; `lifted` sessions honor `Deny` only", "1.0", returns=Deny | Ask,
             subscribers="grant", permission=("hooks:gate", "coding:read")),
    HookSpec("coding.tool.post", "emit", CodingToolPostPayload, "5.12", "a coding session finished a tool call, via `POST /coding-hooks/{session}/post`",
             "`emit`, 1 s hook timeout, never affects the session", "1.0", subscribers="grant", permission=("coding:read",)),
)}
