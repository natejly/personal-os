# Sources of truth

Grain holds calendar events, todos, docs, mail and files. Some of those also live
in a Google account. When the account is connected, which copy is *real*?

Today the answer differs per domain, and nothing says so out loud:

| Domain | Store | Disconnected |
| --- | --- | --- |
| **Events** | Google only (`google.calendar_*`) | nothing — `CalendarView` shows "Showing todos only" |
| **Todos** | SQLite `todos`, two-way mirrored to one Google Tasks list | works fully |
| **Docs** | SQLite `docs` + revisions; Drive/Docs reachable as tools | works fully |
| **Mail** | Gmail only | nothing, and nothing else is possible |

So the calendar is a hole (no Google, no calendar) and the todo sync has a
genuine ambiguity: a field Google Tasks cannot represent — priority, project —
has no defined owner when both sides changed.

This note proposes one rule per domain, a provider seam under the UI, and
non-destructive transitions in both directions.

---

## The rule

> **A domain has exactly one authoritative store at any moment. Connecting or
> disconnecting Google changes which one it is, as an explicit, one-time
> migration — never as an ongoing merge.**

Merging is where sync bugs live. If Google is authoritative, writes go to Google
and the local table is a cache. If local is authoritative, Google is not
consulted. Reconciliation logic runs *once*, at the connect or disconnect
boundary, with the user looking at it.

## Four classes of domain

Not every domain can follow the same rule, because Google's model is not always
equivalent to ours. Naming the classes is most of the design.

**A. Google-authoritative when connected, local otherwise.**
The two models are close enough to swap. *Calendar*, and contacts if it ever
lands. Needs a native store built (we have none) and a provider seam.

**B. Shared core, local sidecar.**
Google owns the fields it can represent; we own the rest, always. *Todos*:
Google Tasks holds title, notes, due, status; `priority`, `project_id`,
`calendar_event_id` are local-only and never lose a conflict, because Google
cannot have changed them. The existing two-way sync stays — this is a statement
of field ownership, not new machinery, and it removes the only real ambiguity
in `gtasks.py`.

**C. Local-only; Google is import/export.**
Google has no equivalent model, so it is never the source of truth. *Docs* (our
revision history, pending assistant diffs and accept/reject flow have no Drive
counterpart), notes, boards, memory, the graph. A doc may hold an optional Drive
link for a manual push or pull; that is a copy, and the UI should say so.

**D. Google-required.**
No local fallback is meaningful. *Mail*, Drive browsing. The honest empty state
is a connect card, not a degraded version.

The mistake to avoid is treating a class C domain as if it could become class A
(syncing docs into Drive round-trip) or leaving a class A domain with no local
store (today's calendar).

## The seam

One protocol per class-A domain, two implementations, one resolver.

```python
class EventStore(Protocol):
    provider: str                  # 'google' | 'local'
    def list(self, start, end, calendar_ids) -> list[Event]: ...
    def get(self, id) -> Event: ...
    def create(self, fields) -> Event: ...
    def update(self, id, patch) -> Event: ...
    def delete(self, id) -> None: ...
    def capabilities(self) -> set[str]: ...
```

`GoogleEvents` wraps the existing `google.calendar_*` methods unchanged.
`LocalEvents` is a new SQLite table with the same `_event_out` shape, so the
renderer's `Event` type does not fork. A `sources.resolve("events")` picks
between them from `google.status()["connected"]` plus a per-domain setting.

**Capabilities, not connection checks.** Right now `CalendarView.tsx` branches on
`google?.connected` in eight places, and `EventEditor` assumes Google fields exist.
A local calendar cannot do guests, Meet links or RSVP. Have the UI branch on
`caps.has('invites')` instead. Then the calendar is one component that works
whichever store is beneath it, and adding a second provider later (CalDAV,
Exchange) touches no view code.

Capability set for events: `create`, `multi_calendar`, `recurrence`, `invites`,
`conferencing`, `reminders`, `colors`, `rsvp`.

## A cache that is never authoritative

Even with Google authoritative, reads should not hit the network on every render.
`remote_cache(domain, provider, remote_id, scope_id, payload_json, etag,
fetched_at)` plus a per-calendar `sync_token` gives incremental refresh, offline
reads for the home widget, and local full-text search over events.

Two invariants keep it from turning into a second source of truth:

- Writes are **write-through**: Google first, then refresh the affected rows.
  Nothing is ever written to the cache and only later pushed.
- Reads carry `fetched_at`, and the UI marks data stale rather than pretending.

**Offline writes: refuse them, at first.** While Google is authoritative and
unreachable, creating an event fails with "Calendar is read-only until you're back
online." The alternative — an outbox of queued intents replayed on reconnect — is
the single largest source of complexity and divergence in this design, and a
desktop app on a laptop is usually online. Ship refuse; add the outbox only if
the refusal actually bites.

## Transitions

The interesting half. Both directions must be non-destructive.

### Connecting Google (local → Google)

Never merge silently. The connect flow asks once per class-A domain that has
local rows:

| Choice | What happens |
| --- | --- |
| **Adopt** (default when the count is small) | push every local record to Google, record `external_id`, then Google is authoritative and the old rows become cache |
| **Keep separate** | local records stay local and are shown beside Google records with a badge; new writes go to Google |
| **Discard** | archive the local table, Google only |

Todos already own the adopt machinery: `all_for_sync()` → `tasks_insert` →
`set_sync_state` in `gtasks.py` is exactly this loop.

### Disconnecting Google (Google → local)

Promote the cache. Every cached record becomes a real local row, keeping its
Google id in `detached_external_id`. Disconnecting stops being a data-loss event
— today it empties the calendar — and it is cheap, because the cache already
holds full payloads.

### Reconnecting

Match on `detached_external_id` first; then fall back to a narrow fuzzy match
(same title, start within a few minutes) so a reconnect re-links instead of
duplicating. Anything unmatched goes through the adopt prompt above.

### Switching accounts

Treat as disconnect-then-connect: promote the cache under the old account, then
run adopt against the new one. Never carry `external_id`s across accounts — they
will 404 and the sync will "delete" the local mirror.

## Telling the user

One endpoint, and the UI stops reading `google.connected` for anything but the
Settings page:

```json
GET /sources
{
  "events": {"provider": "google", "cache_age": 12,
             "capabilities": ["create","invites","recurrence","rsvp"]},
  "todos":  {"provider": "local", "mirror": "google-tasks",
             "last_sync": 1759180000, "owns_locally": ["priority","project_id"]},
  "docs":   {"provider": "local"},
  "mail":   {"provider": "google", "required": true}
}
```

Settings grows one small table — *Calendar: Google · Todos: Grain, mirrored to
Google Tasks · Docs: Grain* — so the answer to "where does this actually live"
is always on screen. Each class-A row carries a "Use Grain instead" switch, which
runs the same promote path as a disconnect without signing out.

## Order of work

1. `sources.py` — the domain registry, `resolve()`, and `GET /sources`. The
   renderer switches to it. No behaviour change, but the seam exists.
2. `local_events` + `LocalEvents`, and `CalendarView`/`EventEditor` branching on
   capabilities. **A real calendar with no Google account** — the visible win, and
   it closes the hole.
3. `remote_cache` + sync tokens for events: offline reads, faster renders, event
   search.
4. Transitions: adopt-on-connect, promote-on-disconnect, re-link-on-reconnect.
5. Write down todos' field ownership and make `gtasks.py` enforce it rather than
   relying on Google never sending those fields back.
