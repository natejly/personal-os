# Parity: code execution, shell, file workspace, microVMs, browser

Date: 2026-10-04. Worktree: `harness-parity`. Grain is compared with ChatGPT code interpreter / data analysis, ChatGPT agent, the Claude code execution tool and claude.ai file creation, Claude Code sandboxing, OpenAI Codex, E2B and Manus.

Spot-checked claims (code read on this branch):

- `run_python` caps a run at 120 s (`tools.py:1132`, `secs = max(1, min(int(timeout), 120))`), and every call is a fresh `mkdtemp` process (`sandbox.py:401`). There is no kernel module.
- The only sandbox setting in the UI is `sandboxMountDesk` (`SettingsModal.tsx:346`). `sandboxNetwork`, `sandboxImage` and `sandboxRuntime` are read in `microvm.py:144,186,387` but have no default in `llm.py` and no control.
- Sandbox tools have display names only (`toolDisplay.ts:45-48`). No `SandboxCard` exists in `toolcards/`.
- No route lists or kills shell jobs (no `/shell` route in `app.py`). `kill_conversation` runs at the end of a plain-chat reply (`app.py:2162`). Orphans are reloaded from `shell_jobs.json` (`shell.py:272`).
- `sandbox_export_file` refuses in a chat without a desk (`tools.py:2006`). Browser downloads go only to `<desk>/work/downloads` (`browser.py:157`).
- `DeskBrowser.tsx` hard-codes `desk:${desk.id}`. The preload `agentBrowser` API (`list/show/hide/subscribe`) takes any session name.
- `snapshots.py:165` skips a root equal to `$HOME`. `MAX_FILE_BYTES = 2 MB` and `MAX_FILES = 50_000` (`snapshots.py:40-41`).
- **New finding.** `mac.allowed_path` (`mac.py:84`) accepts `$HOME` itself as a workspace root, because `rel.parts` is empty. The settings validator (`app.py:789`) uses that check. `shell_profile` (`sandbox.py:84`) then grants `file-write*` on the whole granted root. Its only write denies are `.git/hooks` and `.git/config`, and its data-dir and secret denies are `file-read*` only. With `~` (or any folder holding launch or rc files) as a root, a sandboxed shell command can write `~/.zshrc`, `~/Library/LaunchAgents/*.plist` and `~/.ssh/authorized_keys`, and can truncate the app's own `personal-os.db`. Each of these runs later outside the sandbox, or rewrites Grain's own settings and approvals.

## Feature table

| Capability | Grain | ChatGPT code interpreter | ChatGPT agent | Claude code exec / file creation | Claude Code sandbox | Codex | E2B | Manus |
|---|---|---|---|---|---|---|---|---|
| Isolation unit | Seatbelt per command (run_python, shell). Docker/colima container per conversation (sandbox_*) | Container per conversation | VM per task | Container per request chain (id reuse) | Seatbelt / bubblewrap per command | Seatbelt / bubblewrap locally, container per cloud task | Firecracker microVM | Ubuntu VM per task |
| Stateful Python | No. Stateless, 120 s cap | Yes, within the container's 20 min idle window | Yes (terminal) | Yes from 20260120 (REPL persistence) | n/a | n/a | Yes (Jupyter-style code interpreter) | Yes |
| Programmatic tool calls from code | Yes. 7 tools, 50 calls, approval cards pause the clock | No | No | Yes (20260120+) | No | No | No | No |
| Package install | Shared work venv, wheels only, ask per install | No network, preinstalled only | Yes | API: none. claude.ai: package-manager egress by default | Via proxy allowlist | Setup script has net, agent phase off | Open by default | Yes |
| Network policy | Shell: off / proxy allowlist (registry presets + domains) / open. Sandbox: on/off only, hidden setting | None | Open with confirmations | None (API). Plan-based presets (claude.ai) | Proxy allowlist, prompt on first new domain | off / restricted presets / all, optional GET-only | allow/deny lists, live update | Open |
| Write scope on host | Desk workspace + workspaceRoots. Only `.git/hooks` and `.git/config` protected | n/a | n/a | n/a | cwd + temp + added dirs. rc files, `.gitconfig`, agent config and MCP config always write-denied | workspace + writable_roots, `.git` protected | n/a | n/a |
| Unavailable-sandbox behavior | Refuse unless `unsandboxed=true`, which always asks | n/a | n/a | n/a | Falls back to unsandboxed unless `failIfUnavailable` | Falls back to a bundled helper | n/a | n/a |
| Background jobs | 4 live, poll/kill. Chat jobs die at reply end. No jobs view | No | n/a | No | Yes (Monitor) | Yes | Yes (background commands) | Always-on Cloud Computer (paid) |
| Checkpoints / persistence | Container fs checkpoints (3), stopped after 300 s idle, kept 14 days, LRU 5 | 20 min idle then gone | Per task | ~5 min idle checkpoint, 30-day restore | n/a | Per task | Pause/resume with memory, no TTL | Sleep/awake, recycled after 7/21 days |
| Resource limits surfaced | Hard-coded 1 GB / 2 CPU, not shown | Memory tiers 1-64 GB | Plan task caps | 5 GiB RAM / disk, typed timeout error | n/a | n/a | Per template | n/a |
| Files in | Typed text, document text, desk mount | Upload up to 512 MB | Upload + browser download | Files API container_upload | Host fs | Host fs | SDK filesystem | Upload 512 MB |
| Files out | Desk workspace only (export, downloads). Inline images from run_python | Download citations | Downloadable artifacts | Files API download, 30 MB | Host fs | Host fs / PR | SDK | Artifacts restored on recreate |
| Undo of agent writes | Per-file snapshots + git-backed per-reply Undo/Redo | n/a | n/a | n/a | Checkpoints (rewind) | git | n/a | n/a |
| Browser | Hidden Electron window via CDP, a11y refs, forced cards for submit/password/payment/download, handoff | n/a | Visual + text browser, live view, takeover | n/a | n/a | n/a | n/a | Cloud browser with takeover, local-browser operator |
| Live browser view | Desk only | n/a | Always | n/a | n/a | n/a | n/a | Always |
| Saved logins management | `persist:agent` cookies, no UI to clear | n/a | n/a | n/a | n/a | n/a | n/a | Settings page to manage saved logins |
| Output artifacts | HTML under a no-network CSP, versioned | Files | Files / slides / sheets | docx/xlsx/pptx/pdf | n/a | n/a | n/a | Slides / web |

## Where Grain is ahead

- **Approval inside code.** The toolbridge lets a sandboxed script call app tools under the same gates as the model, and the clock pauses while an approval card waits. Only the newest Claude code execution versions have anything like it, and those run in the cloud.
- **Undo.** Per-file snapshots plus per-reply whole-folder Undo/Redo, with `edited_since` conflict reporting. None of the hosted peers offer an undo of host-file changes.
- **Browser risk gating.** Every click, type, select or press is previewed, and submit, password, payment and download raise a forced card. The ChatGPT agent asks only before "consequential" actions, judged by the model.
- **Egress reporting.** The proxied shell reports contacted and blocked hosts per run, and network use taints the reply, so later actions ask.
- **Read-before-write ledger and secret denylists** on the fs tools and the shell.

## Different by design

- Local first. No hosted VM per task. The microVM is the user's own colima, and its reaper and LRU cap (5) keep a laptop from filling with VMs.
- No pixel control. The browser acts on CDP accessibility refs, and the agent never signs in; the user takes over instead.
- No always-on agent VM and no heartbeat. Desks wake on notes, not on a timer.
- Artifacts get no network on purpose (opaque origin, `connect-src 'none'`). Peers' artifacts are files, not live pages.

## Gaps

1. **(P0) Shell write sandbox protects too little.** See the finding above. Peers deny writes to rc files, `.gitconfig`, launch and agent config even inside writable dirs.
2. **(P1) No stateful Python.** Every peer with a code interpreter keeps a session.
3. **(P1) Sandbox settings are hidden, and there is no view of sandboxes.** Network, image and runtime cannot be changed from the UI, and nothing shows what is running.
4. **(P1) No view of running shell jobs.** Orphans survive restarts invisibly.
5. **(P1) Files out in a plain chat.** sandbox export and browser downloads are refused. Peers return downloadable files in every conversation.
6. **(P1) Browser live view only in desks.** Peers show the agent's screen wherever it runs.
7. **(P2) Sandbox has no tool card.**
8. **(P2) Sandbox network is all-or-nothing.** Peers use allowlist presets.
9. **(P2) No way to clear the agent browser's cookies.**

## Sources

- https://developers.openai.com/api/docs/guides/tools-code-interpreter
- https://help-lb.openai.com/en/articles/8555545-file-uploads-faq
- https://www.zo.computer/tutorials/code-interpreter-session-expired-fix-it-or-get-a-better-alternative
- https://help.openai.com/en/articles/11752874-chatgpt-agent
- https://www.techtarget.com/whatis/feature/ChatGPT-agents-explained
- https://www.tomsguide.com/ai/openais-new-chatgpt-agent-is-here-5-features-that-change-everything
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool
- https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude
- https://tanstack.com/ai/v0/docs/tools/provider-skills
- https://code.claude.com/docs/en/sandboxing
- https://github.com/anthropics/sandbox-runtime
- https://learn.chatgpt.com/docs/sandboxing
- https://developers.openai.com/codex/agent-approvals-security
- https://learn.chatgpt.com/docs/cloud/internet-access
- https://developers.openai.com/codex/security
- https://docs.e2b.dev/sandbox/persistence
- https://docs.e2b.dev/sandbox/internet-access
- https://docs.e2b.dev/faq/sandbox-lifetime
- https://docs.e2b.dev/sandbox/auto-resume.md
- https://e2b.dev/blog/customize-sandbox-compute
- https://www.manus.im/blog/manus-sandbox
- https://help.manus.im/en/articles/15392111-what-is-the-cloud-computer
- https://manus.im/docs/integrations/manus-browser-operator
- https://manus.im/help/cloud-browser
- https://help.manus.im/en/articles/11711226-how-can-i-manage-the-login-information-that-manus-stores
