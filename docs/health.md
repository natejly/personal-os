# Health

The Health page (sidebar row, heart icon) tracks a small set of daily metrics, shows a Today
card, and gives the assistant `health_summary` / `health_log` / `health_delete_entry` (tool group `health`).

## Metrics

Each metric has a **kind** (`number`, `scale` 1–5, `check` yes/no) and a **per-day rule**:

- `sum`: readings add up (water, steps).
- `last`: the latest reading wins (weight).
- `avg`: readings are averaged (mood).

Readings are filed under a local calendar day, so last night's sleep goes under the day you woke up.
Built-in metrics can be hidden or re-goaled but not deleted. Custom metrics can be deleted, along with their history.

## Connected services

**Health → link icon** connects a fitness account through an MCP server. Its tools are also
available to the assistant like any other connector.

| Service | How | Gets |
|---|---|---|
| COROS | Official hosted MCP server (`https://mcp{us,eu,cn}.coros.com/mcp`); browser sign-in (OAuth, PKCE), nothing stored but the token | sleep, steps, resting HR, workouts |
| Garmin | Community [`Taxuspt/garmin_mcp`](https://github.com/Taxuspt/garmin_mcp) via `uvx` (unofficial; Garmin has no public API for individuals) | sleep, steps, resting HR, workouts, weight |

Garmin needs a one-time login with MFA in a terminal before connecting:

```
uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
```

Strava is not offered. Its API agreement (Nov 2024) prohibits using Strava data in AI applications.

### How sync behaves

- **Approve first.** Nothing is called until you approve the list of read tools a source will use.
  Approval is pinned to each tool's schema hash. If the server later changes a tool, sync skips it
  and the panel says "changed" until you approve again.
- **Numbers only.** Answers are parsed for dates and numbers. No text from them is fed to a prompt by sync.
  Values outside a sane range (e.g. 7 minutes of "sleep" from a seconds/minutes mix-up) are dropped.
- **No duplicates.** A source has one reading per metric per day, replaced on every sync. For summed
  metrics the day's value is the largest single source (your own entries count as one source), so
  a night logged by hand and synced from a watch is never counted twice.
- **Background.** Approved, enabled ("Auto") sources re-sync the last 7 days every 6 hours.
  "Sync now" does it immediately. Anything a tool returned that couldn't be read shows under
  "problems" with a short sample.

COROS publishes its tool schemas only after sign-in, so its arguments come from each tool's advertised
input schema and its fields are matched against likely names. If a COROS metric doesn't show up after
your first sync, the problems list shows what came back.

## Remote MCP servers (general)

`mcp_client` now supports streamable-HTTP servers, not just stdio. OAuth lives in `mcp_oauth.py`:

- `POST /mcp/servers/{id}/sign-in` returns `auth_url`, and the renderer opens it.
- The loopback `GET /mcp/oauth/callback` (public, but it only completes a sign-in this app started)
  delivers the code.
- Tokens are kept in `mcp_oauth`, never in a server's `secrets`.

A background connection never opens a browser. It refreshes its token or stops with "sign-in required".
