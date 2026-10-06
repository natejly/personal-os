# Daily digest

One quiet row a day in the Agent Inbox, under **While you were away**. No OS
notification, no modal, and it does not add to the Today badge. Mark it read like
any job run.

## What it says

Only lines with something in them; a day with nothing to say writes no row.

- **Meetings:** recordings finished in the last 24 h, and enhance proposals waiting
  for review.
- **Activity:** time in apps over the last 24 h and the top three apps, summed from
  the raw focus events (private windows count toward the total, never by name).
- **Setup:** a permission that is quietly keeping an on-by-default module from
  working, each with one button to the place that fixes it:
  - Microphone not granted → Settings → Meetings (Grant button).
  - Accessibility not granted → the Activity view's Capabilities.

No model call: the text is assembled from counts (`digest.assemble`).

## When

At the hour in Settings → Meetings → Daily digest (default 08:00), or at the first
read of the inbox after it. At most one per local day and never within 20 h of
the previous one, so relaunching the app does not repeat it. The check rides on
`GET /inbox` (at most every 10 minutes), so nothing runs while nobody looks.

Settings key `digest`: `{"enabled": true, "hour": 8}`.

## Where the row comes from

`digest.write` stores a finished `agent_runs` row of kind `job` with no job behind
it (`input.kind = "digest"`, `input.links` for the fix buttons) and the body as
its reply event. The inbox reads it like any job run; the notification feed
speaks only for failures and proposals, so it stays silent.

Tests: `backend/tests/test_digest.py`, `src/renderer/src/lib/inboxBadge.test.ts`.
