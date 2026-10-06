# Parity: permissions, approvals, prompt-injection defenses, verify-after-write, undo

Date: 2026-10-04. Worktree: `harness-parity`. The Grain facts come from a code read with spot checks, and I re-checked every gap below by hand. The peer facts come from the vendors' public docs, cited in the table. This file lives under `docs/research/`, so it names other products; no file outside this folder should.

**Peers compared:**
- **CC**: Claude Code (permission modes, rules, hooks, checkpoints).
- **CGA**: ChatGPT agent / Atlas agent, since replaced by ChatGPT Work.
- **Codex**: OpenAI Codex (CLI, IDE, cloud).
- **CiC**: Claude in Chrome.
- **Gem**: Gemini Agent and Gemini in Chrome agentic browsing.

| Peer | Sources |
|---|---|
| CC | https://code.claude.com/docs/en/permissions · https://code.claude.com/docs/en/permission-modes · https://code.claude.com/docs/en/hooks · https://code.claude.com/docs/en/checkpointing · https://anthropic.com/engineering/claude-code-auto-mode |
| CGA | https://help.openai.com/en/articles/11752874-chatgpt-agent · https://help.openai.com/en/articles/12628199-using-ask-chatgpt-sidebar-and-chatgpt-agent-on-atlas · https://openai.com/index/introducing-chatgpt-agent/ |
| Codex | https://learn.chatgpt.com/docs/agent-approvals-security · https://learn.chatgpt.com/docs/security · https://learn.chatgpt.com/docs/cloud/internet-access |
| CiC | https://support.claude.com/en/articles/12902446-claude-in-chrome-permissions-guide · https://www.anthropic.com/news/claude-for-chrome · https://anthropic.com/research/prompt-injection-defenses |
| Gem | https://support.google.com/gemini/answer/16596215 · https://support.google.com/gemini/answer/16730149 · https://blog.google/security/architecting-security-for-agentic/ |

## Feature table

| Capability | Grain | CC | CGA | Codex | CiC | Gem |
|---|---|---|---|---|---|---|
| Autonomy modes | Each tool is on, ask or off, defaulted by its danger tier. A "skip permissions" switch (per chat or global) cannot lift external, schedules, forced or shell cards | Six modes: manual, acceptEdits, plan, auto, dontAsk, bypass | Confirms consequential actions; watch mode | Approval policy combined with sandbox mode, plus presets | Manual / Automatically approve / Skip all | Confirm each item, or Confirm all |
| Actions that always ask | The external, plan and schedules tiers default to ask. **The user can still switch an external tool to "on" in the Settings, Project or Chat overrides** (`tools.py` `Toolbox.effective`) | Ask rules, `requiresUserInteraction`, rm on critical paths | Purchases, sends, submits | Escalations outside the sandbox | Purchases, deletes, permission changes, account creation, sensitive data, downloads. Asks whatever the grant | Sends, data changes, purchases, form submits, edits to shared docs |
| Rule language | `Tool(pattern)` allow/ask/deny, deny > ask > allow. Splits shell lines into subcommands, refuses HARDLINE commands outright, suggests narrow rules (`permrules.py`) | Same precedence. Matches subcommands and env assignments; a Read deny also blocks writes; symlink-aware | none | execpolicy prefix rules; the most restrictive match wins | Per-site grants | none |
| Reviewer model in place of the user | none, by design | Auto-mode classifier; tool results are stripped from its input | Injection monitor | Auto-review agent; fails closed | Safety check in auto mode | User Alignment Critic, which sees only metadata |
| Grant scopes | Once, chat session (held in memory), chat, global, or a patterned rule. MCP grants are bound to the tool's schema hash | Once, session, or persistent per repo | n/a | Per policy | Once, or per site | Per task |
| List and revoke grants | Rules in Settings; tool modes in three toggle UIs; MCP grants under Connectors. **Session grants and approval history are not visible anywhere** | /permissions | Admin activity view (Work) | Config file | Settings page listing always-allowed sites, with revoke and a permission history | n/a |
| Protected config paths | Writes are refused in dot-folders, ~/Library and the app's data folder (`mac.allowed_path`). The agent cannot edit its own permissions | .git, .claude, .vscode and similar are never auto-approved | n/a | .git, .codex, .agents stay read-only | n/a | n/a |
| Untrusted content | Taint tracking: once a run reads untrusted content, external, network, schedules and lasting-text calls become forced asks, and fetches are limited to URLs the user typed or a search returned. **Tool results carry no data delimiters** | Server-side injection probe; the classifier never sees tool results | Monitoring plus refusal training | Network off by default | Classifiers plus training | Injection classifier, origin sets, critic |
| Network egress | SSRF-safe fetch; URL allowlist on tainted runs; Seatbelt shell behind an allowlisting CONNECT proxy | OS sandbox with a domain allowlist | Remote VM | Off by default; domain and HTTP-method allowlist | Site blocklists | Origin sets |
| Keeping credentials away from the model | Command and MCP output is scrubbed of secrets; the backend holds the Google OAuth tokens | Sandbox | Takeover mode | Sandbox | The user types sensitive data | Take control |
| Plan approval | `propose_plan` binds each step to an argument digest; taint the plan did not predict voids it | Plan mode | n/a | n/a | n/a | User reviews the sites and data before it starts |
| Unattended runs | Jobs can only propose (two gates); `unattendedApprovals=deny` | dontAsk | Work schedules | `never` policy | n/a | Scheduled tasks with pause and resume |
| Editing arguments before approving | Mail, calendar, Tasks and write_local_file cards, validated by `approval_edits`. **Job proposals accept edits with no validation** | Hook `updatedInput` | Takeover | n/a | n/a | n/a |
| Verify after write | Every google.py write is read back; an unproven write is an error (`verify.py`) | none | none | none | none | none |
| Undo | Gmail outbox (60–120 s), file pre-images, git-backed folder snapshots per run (shell side effects included), 30-day trash, doc revisions | Checkpoints for its own edit tools, not Bash | none | /undo was dropped | none | none |

## Where Grain is ahead

- **Verify-after-write.** No peer documents an automatic read-back after an external write. Grain reads back every Google write; if the read-back does not prove it, the call is an error and the model is told not to retry it.
- **Undo breadth.** Grain's run snapshots cover shell side effects, which CC checkpoints explicitly leave out. None of the agents surveyed has anything like the undo-send outbox.
- **Taint as a gate.** Peers rely on probabilistic classifiers. Grain's taint flag is deterministic:
  - once untrusted content is read, outward calls cannot run without a card;
  - a plan approved earlier is voided by taint it did not predict.

  This suits open models, which cannot host a trustworthy classifier.
- **Schema-pinned MCP grants and drift quarantine.** No peer ties a grant to the tool's schema.

## Different by design

- **No reviewer-model mode.** Auto-review in the peers depends on a strong classifier model. Grain runs on Fireworks open models, so it uses deterministic taint gating and argument-pattern rules instead. This is not a gap.
- **No takeover browser or watch mode.** Grain has no remote browser. The local `<webview>` and browser tools use allowlists instead.
- **Standing anti-goals:** no blanket bypass, no agent editing its own permissions, no auto-sending mail from jobs.

## Gaps, ranked

1. **P0: an external tool can be switched to "on".**
   - `Toolbox.effective` (`backend/personal_os/tools.py:736`) accepts any mode from the global, project or chat overrides.
   - `PUT /settings`, `PUT /projects/{id}` and `PATCH /conversations/{id}` accept it without complaint.
   - The ToolPermissions toggles offer "on" for these tools.
   - The approval card already refuses "Always" for external tools, but these three settings paths do not.
   - Peers keep consequential actions on ask whatever grants the user has given (CiC, Gem, CGA).
2. **P0: edits on a job proposal skip validation.** `POST /proposals/{pid}/accept` (`app.py` ~4266) passes `args` straight to `jobs.Proposals.claim`. It runs neither `approval_edits.validate` nor the EDITABLE_TOOLS check, both of which an edit on a chat card goes through.
3. **P1: no view of grants and history.**
   - Session grants (`permrules.SessionGrants`) live only in memory, cannot be listed or revoked, and vanish on restart.
   - Decided approvals are stored but never shown.
   - Peers: CiC has a revocable site list with a permission history; CC has /permissions.
4. **P1: no data delimiters on untrusted tool results.** `working.ToolResults.render` and the tool message in `_chat_stream` (`app.py` ~2989) pass raw text to the model. Wrapping the text in tags carrying a per-run nonce ("spotlighting") is cheap and works with any model.
5. **P1: MCP tools that write can get standing grants.** At `app.py` ~2811 the condition is `standing = … (danger != "external" or mcp_is(name))`, which lets an MCP write tool keep a standing "Always" grant. CiC and Gem keep consequential actions on ask whatever the grant, and the user's rule is that external actions keep asking.
6. **P1: three settings have no UI.** `fetchAllowlist`, `unattendedApprovals` and `snapshotsEnabled` appear only in `shared/types.ts`.
7. **P2: two small rule-tool problems.** The rule tester only handles shell commands. MAIL_TOOLS (`permrules.py:36`) names gmail_reply and gmail_forward, which do not exist.
8. **P2: no undo for calendar and Tasks writes.** No peer offers this either. Grain already journals and verifies these calls, so an inverse operation is within reach.
9. **P2: local file writes are not read back.** They could reuse the UNVERIFIED pattern the Google writes already use.
