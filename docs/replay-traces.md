# Replay traces

Grain records one structured trace per chat request, plus follow-up and boundary events, so a later replay or regression run can re-create what happened. Everything stays on this machine: no network call, no new dependency.

## Where

`<data dir>/traces/YYYY-MM-DD.jsonl` (UTC date), append-only, one JSON object per line. The data dir is the same one the database uses (`PERSONAL_OS_DATA_DIR`). Writes happen on a background thread under a lock, and any failure is logged at warning level and dropped; a chat never waits on or fails because of its trace.

Every line has `"schema": 1`, `"type"`, `"ts"` (ms since epoch), `session.id` (one per backend process) and `gen_ai.conversation.id`. Names follow the OpenTelemetry GenAI conventions where one exists, `grain.*` otherwise.

## Privacy

Free text (prompt, reply, tool arguments, errors, correction text) is run through `redact.scrub_secrets` and capped at 20,000 characters. Dict keys that look like credentials (api key, token, secret, password, authorization, cookie, bearer, private key, session id) are dropped. The system prompt, offered skills and injected memories are stored as SHA-256 hashes only.

## `type: "trace"`

One per reply, written when the reply finishes (ok, error or stopped), including a failed setup or a stop during history compaction.

| Field | Meaning |
| --- | --- |
| `trace_id` | fresh id |
| `gen_ai.response.id` | assistant message id |
| `grain.message.user_id` | user message it answers |
| `service.version` | app version |
| `grain.prompt.hash`, `grain.skills.hash`, `grain.memory.hash` | hashes of the stable system prompt, offered skills (id, name, description, disclosure), injected memory ids and content |
| `grain.model.tier_requested` | `auto`, `high`/`medium`/`low`, `default` or `custom` |
| `gen_ai.request.model` | model asked for before auto-routing |
| `gen_ai.response.model` | model that ran (the last model round's, else the routed model) |
| `grain.context.sections` | `[{name, tokens}]`: `stable_prefix`, one entry per volatile block named from its first line, plus `total`; token estimates computed on the trace worker thread |
| `gen_ai.input.messages`, `gen_ai.output.messages` | the user text and the final reply |
| `tool_calls` | `[{gen_ai.tool.name, gen_ai.tool.call.id, gen_ai.tool.call.arguments, grain.tool.outcome (ok/error/denied/parked), error.type, grain.tool.error, grain.latency_ms}]` |
| `spans` | `[{id, name, kind, start, end, latency_ms, parent_id, error}]` from the reply tracer |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.reasoning.output_tokens` | summed over model rounds |
| `grain.cost.usd` | the run meter's cost, null when the model is unpriced |
| `grain.latency_ms` | first span start to last span end |
| `grain.outcome`, `error.message` | `ok`, `error` or `stopped` |

A steer that splits a reply into segments traces only the final segment.

## `type: "followup"`

`gen_ai.response.id` (the assistant message affected), `grain.followup.kind` in `edit`, `retry`, `regenerate`, `correction`. A correction also carries `grain.correction.text`, the redacted literal message. A new user message directly after an assistant reply counts as a correction when it starts with a phrase such as "no", "nope", "wrong", "incorrect", "actually", "instead", "that's wrong", "not what I", "I meant" or "you forgot" (`replay_trace.is_correction`).

## `type: "boundary"`

`grain.boundary.kind` is `clear` (`POST /conversations/{id}/clear`) or `compact` (manual or automatic compaction, with `grain.compact.tokens_before` and `grain.compact.tokens_after`).
