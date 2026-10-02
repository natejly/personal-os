## Coworking, round two: what a desk still lacked, and what we chose to build

Researched 2026-10-02. This follows `sota-cowork.md` (permission rules, sandboxed shell, file tools, subagents,
workflows, undo, the Python tool bridge — all shipped). Six reports sit beside this file in `cowork-expansion/`:

| Report | Covers |
|---|---|
| `knowledge-work-product.md` | Anthropic's Cowork product, Claude Code's tool inventory, UX and safety patterns |
| `hermes-agent.md` | A source read of NousResearch/hermes-agent beyond what round one covered |
| `open-source-agents.md` | Tool inventories of OpenClaw, OpenHands, Goose, Codex CLI, Gemini CLI, Cline/Roo, opencode, with a gap matrix |
| `browser-automation.md` | Accessibility-snapshot browsing, driving an Electron window over CDP, safety, a tool set |
| `execution-environments.md` | Egress allowlisting, stateful shells and kernels, isolation on macOS, environment bootstrap |
| `long-running-work.md` | False "done" claims, completion gates, progress files, office deliverables, multi-agent evidence |

Each report marks what it verified against a primary source and what it did not; read those markers before relying
on a number.

### Where the reports agree

1. **The first problem is finishing, not tooling.** False completion is the dominant failure in long tasks
   (45–75% of failures depending on the benchmark); structural checks on external state and an independent reviewer
   cut it sharply, while self-assessment by the same model does not. A code audit of our own desks found the
   same shape of problem from the other side: desks rarely chained past their first reply, and were never told what
   tools they had.
2. **A browser the agent can drive is the largest missing capability.** Every reference agent that has one
   converged on the same design: an accessibility-tree text snapshot with element refs, a fresh snapshot after each
   action, a separate cookie profile, and forced confirmation for submits, credentials, downloads and uploads.
   Screenshot-and-coordinates loops need a vision model and do worse on weaker ones.
3. **Network should be an allowlist, not a switch.** A proxy outside the OS sandbox, hostname-filtered, with
   package registries as a preset, is how sandboxed shells install packages without opening the network.
4. **Pictures have to reach a model somehow.** With mostly non-vision chat models, the workable pattern is a tool
   that asks a vision-capable side model about an image and returns text, with OCR as the floor.
5. **Documents need know-how and a mirror.** Per-format guidance plus render-to-image so the agent can check its
   own output is what moves quality on office deliverables.
6. **Multi-agent pays for wide, independent, read-only work** and costs heavily elsewhere. No agent teams.

### What we built this round

| Area | Decision |
|---|---|
| Desk loop | Chain on any per-reply budget stop (not only the round cap), with or without a plan, guarded by progress and the desk caps; one nudge when a reply just trails off; a capability manual assembled from the tools actually offered; `work/PROGRESS.md` carried into every chained turn |
| Finishing | `desk_done` is refused (twice at most) while todo or plan steps are open or a deliverable is missing, empty or changed; one read-only reviewer pass against the brief may send the desk back once; `desk_ask` gains choices |
| Workspace | `desk_read_file` satisfies the read-before-edit ledger and reads documents; `run_python` runs in the workspace; `desk_fetch_file` downloads into it; a shared work environment carries the data and document libraries |
| Shell | Egress through a local allowlisting proxy (registries preset, user hosts); no card for sandboxed commands inside the desk's own workspace; the working directory persists; a foreground timeout moves the command to the background instead of killing it |
| Browser | A per-desk window in the agent's own cookie jar, driven over CDP from the main process; eight `browser_*` tools; consequential actions ask; a human handoff for sign-ins |
| Vision | `view_image` through a vision-capable model, OCR fallback; spreadsheet, slide, image and scanned-PDF text extraction |
| Deliverables | `convert_document`, `render_preview`, `doc_guide` |

### What we deliberately left out

- Pixel-level control of the desktop, and attaching to the user's own browser profile.
- A model that approves commands on the user's behalf, and any "skip all approvals" mode.
- TLS interception in the egress proxy (credential swapping, content inspection).
- A persistent Python kernel and an interactive PTY: files in the workspace carry state between calls for now.
- Agent teams, mailboxes between agents, heartbeat loops.
- JavaScript evaluation and raw protocol access as browser tools.
- Link-following in `fetch_url` after untrusted content: unchanged; the browser is the way to follow a link.
- Bundling LibreOffice: office files can be produced without it, but not rendered for a visual check.

### Open questions carried forward

- Per-site standing approvals for the browser, and how they compose with permission rules.
- Whether a desk's plan should carry a domain allowlist.
- A stored-credential mechanism the model never reads (today sign-in is a human handoff).
- Live output for long-running tools in the desk UI.
- An internal evaluation set to calibrate turn budgets per model.
