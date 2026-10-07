# UX streamlining review — 2026-10-07

Branch `ux-streamline`, cut from main `5da138c0` (after PR #70). The ask: review the whole app and simplify copy,
buttons and bloat without losing anything.

**Method.** The `design-review` skill (gstack, v2.0.0) supplied the audit: first impression, the trunk test on
every surface, the ten-category checklist (hierarchy, typography, colour, spacing, interaction states, content
and microcopy, slop patterns), and its rules for app UI: utility language, calm surface hierarchy, "if deleting
30% of the copy improves it, keep deleting". The `ui-ux-pro-max` skill (v2.13.0) supplied the token and
guideline lookups used while fixing: contrast for muted text in dark mode, compact-label overflow (a chip stays on
one line and never wraps), visible labels on icon-only controls, and empty states that carry one action.

**Evidence.** A throwaway Grain (the e2e harness: own data dir, own port, mock provider, window never
foregrounded) was driven by four scripts under `tests/e2e/shots/` that capture 1280×800 PNGs into
`docs/ux-shots/before/` (≈130 shots) and, after the fixes, `docs/ux-shots/after/`. Rerun any of them with
`SHOTS_PHASE=after node tests/e2e/shots/<area>.mjs` (Node 20). Each run also dumped every visible label and
text block, so the findings below name real strings.

**Scope guard.** Nothing behavioural changed: the review-before-sending mail card, approval cards, the
Allow-everything confirm, the permission mode and Allow-all-domains gates are untouched. Every change is copy,
styling, or an empty-state swap; the one relocation is listed in the table at the end.

## First impression

The app communicates competence and calm: a quiet dark shell, one accent, a left bar that answers the trunk
test on every page (site, page, sections, options, "you are here" via the active row). The first three things
the eye lands on from Today are the greeting ("Still up?"), the Ask box, and the Agent inbox card, which is the
intended order. In one word: *dense*. Where it loses the reader is in the second layer: footers, headers and
settings panes that explain themselves in sentences, badges that say "0", and chips that repeat the default.

Scores by the skill's rubric, before → after: Design **B → B+**, AI-slop **A** (no gradients, no card
mosaics, no happy talk; the cards on Today earn their place as separate data sources). Content quality was the
weakest category (C) and is where most of the fixes landed.

## Findings by surface

Priority: **High** = seen on every visit or misleading; **Med** = clutter on a frequently used surface;
**Low** = polish, noted for later. "Fixed" marks what this branch changed.

### Shell and sidebar

| # | Finding | Priority | Status |
|---|---|---|---|
| S1 | Lists row shows "0" when there is nothing open; a count that means "nothing" is noise next to Today's "3 unread" badge. | High | Fixed: hidden at zero |
| S2 | Each Space row shows its window count, including "0" for an empty space, in the same badge style as the Lists count (different meaning, same look). | Med | Fixed: hidden at zero |
| S3 | Empty Spaces and Projects groups keep their header, "+" and a three-line "No projects yet." block. | Low | Kept (the + is the way to create one) |
| S4 | "Chats / Documents" segment plus bell plus search in one 260px row. Works, but the bell's state is only its colour. | Low | Kept |
| S5 | Right-click menu on a chat row is well scoped (Rename, Pin, Move, Export, Copy, Don't learn, Archive, Delete). | — | Good |

### Today

| # | Finding | Priority | Status |
|---|---|---|---|
| T1 | **Bug.** "Brief me" with no calendar connected sent the literal text `${pimName}` to the model (single-quoted template in `HomeView.tsx`). | High | Fixed |
| T2 | The Health card rendered nine rows of "—" when nothing was logged: a wall of dashes that reads as missing data. | Med | Fixed: one line, "Nothing logged yet today", and only logged metrics listed once some are |
| T3 | The connect strip said "Connect Google" twice: bold prefix in the sentence and again on the button. | Med | Fixed: sentence names the payoff, button names the action |
| T4 | Header carries refresh, customise, Brief me and Quick chat; at 820px Brief me collapses to a bare sparkle. | Low | Kept (each has a title) |
| T5 | Greeting H1 plus "Today · Wednesday, October 7" in the header: two headlines. | Low | Kept (the greeting is the page's one moment of voice) |
| T6 | Daily recap shows raw `Facts: {...}` JSON under the mock provider only. | — | Harness artefact |

### Chat

| # | Finding | Priority | Status |
|---|---|---|---|
| C1 | **Faint allow-all pill.** `.skip-perms.on` set the red colour and tint, but the later `.ghost-btn.on` rule at the same specificity reset it to grey; only a 45% inset border survived. Same for "All domains + MCP". | High | Fixed: three-class selector, stronger tint and border |
| C2 | Footer chips came in two styles: model, Reasoning, Plan and Autonomous as quiet 24px controls; Private and the mode pill as bordered 13px buttons. | Med | Fixed: one chip style in the footer, state still coloured |
| C3 | "Regenerate" was a boxed button under every reply, pulled up into the hover-action row (`margin-top: -10px`). | Med | Fixed: quiet text control with 4px breathing room; follow-up chips keep their outline |
| C4 | Context drawer: "Save this chat as a skill… A single reply has the same button. Either way it waits in Library → Skills until you approve it." | Med | Fixed: "Waits in Library → Skills until you approve it." |
| C5 | Quick chat (⌘I) opened with "Whatever you ask goes out with what is on screen behind this panel." | Med | Fixed: "Sees what is on screen behind this panel." |
| C6 | Tool calls nest three deep (group → row → Details). Two clicks before content. | Med | Kept: the fold is PR #59's design; opening by default would undo it |
| C7 | The approval card shows Approve, Deny, "⌘↵", a pattern box, two "Don't ask again" links, "Deny with a note…" and Details. | Med | Kept: approvals are out of scope by the brief |
| C8 | The mail review card says "waiting for you to send" in the header and "Approval is required each time." in the footer. | Low | Kept: the card's copy is part of the safety contract |
| C9 | The autonomy control is "Autonomous" on screen, "Work autonomously" as its accessible name. | Low | Kept (the e2e suite addresses it by the accessible name) |
| C10 | The paperclip opens the native file dialog directly; its title says "Add files to this chat". | — | Good |
| C11 | Under Allow everything with All domains on, the footer carries seven chips and wraps to two lines at 1280px. | Low | Kept: both red pills are the only cue for that state, so they stay full-width |

### Lists, Calendar, Mail, Health

| # | Finding | Priority | Status |
|---|---|---|---|
| V1 | Mail toolbar had two identical refresh icons within an inch: header "Refresh mail" and the watch chips' "Re-scan recent threads". | Med | Fixed: re-scan uses a scan icon and says what it scans for |
| V2 | "Needs reply 0 · Awaiting reply 0" chips show zero counts. | Low | Kept: each chip opens its own section |
| V3 | Calendar header: Today, prev, next, Week/Month, range, calendars panel, refresh, Plan my week, New event. | Low | Kept; icon-only below 1000px already |
| V4 | Lists left rail shows "All" and "Todos" with the same count when there is one list. | Low | Kept |
| V5 | Health tiles show "—" as the headline with yesterday's value in small text. | Low | Kept (the value shown is today's by design) |

### Library

| # | Finding | Priority | Status |
|---|---|---|---|
| L1 | A 60-word paragraph explaining what MCP is sat above every Connectors sub-tab, pushing the catalog down. | High | Fixed: one line; the explanation lives on the MCP link's hover and in `docs/connectors.md` |
| L2 | "Every tool asks before it runs" appeared three times (header, Import dialog, Install form). The Import and Install copies were static and wrong under Auto and Allow everything. | High | Fixed: header is mode-aware (it already was), Import says only what it does, Install form line removed (the install toast already states the mode) |
| L3 | Every catalog card carried "Runs here", "No sign-in" and its category: the default, the default, and the filter directly above. | Med | Fixed: chips only for exceptions (Remote, API key, Browser sign-in, Needs settings) |
| L4 | Automations tab holds Workflows and Commands; scheduled jobs live on Today under "Scheduled (n)". | Low | Kept (naming only; moving jobs is a feature change) |
| L5 | "Model Context Protocol" as the publisher wraps to two lines on narrow cards. | Low | Kept |

### Files and Projects

| # | Finding | Priority | Status |
|---|---|---|---|
| F1 | Doc toolbar: Read pill, three view icons, five unlabelled icons; wraps to two lines at 820px. | Low | Kept (each has a title; re-cutting the toolbar is a design task) |
| F2 | "Trash" in the Files tree opens Settings → Advanced → Data. | Low | Kept |
| F3 | Project Context tab shows a BY TYPE / BY CHAT toggle for two items. | Low | Kept |

### Settings

| # | Finding | Priority | Status |
|---|---|---|---|
| P1 | Permissions: each mode card repeated what the closing paragraph says ("own data off limits", "ask first"); Allow everything ran to two lines. | Med | Fixed: one clause per card; the closing paragraph is the single statement of what always stays protected |
| P2 | Advanced → Approvals opened with two paragraphs and an On/Ask/Off legend before the first control. | Med | Fixed: one line each |
| P3 | Advanced → Approvals pane overflows sideways (horizontal scrollbar). | Low | Noted; the rules table is wider than the pane |
| P4 | Memory list layout: scope select, Learning disclosure, counts, search, four-way layout switch, helper paragraph, History, Tidy up/Export/Import, then cards each with a type select, two badges, a date and three icons. | Low | Kept: PR #69 shipped this layout last week; worth its own pass |
| P5 | Advanced → Data: six headings run together ("Data", "Export all data", "Data folder", "Space presets", "Trash", "Diagnostics"). | Low | Kept |

### Onboarding, Help, overlays

| # | Finding | Priority | Status |
|---|---|---|---|
| O1 | Every wizard step shows Set up later, Back, Continue and the hint "Enter to continue, Esc to go back". | Low | Kept (the hint is the keyboard affordance) |
| O2 | Help lists "Keyboard Shortcuts…" and "Keyboard Shortcuts (outside a text field)" as two rows. | Low | Kept |

## Relocated controls

Every removed element and where it now lives. Nothing lost its only path.

| Removed from | What | Now reachable at |
|---|---|---|
| Library → Connectors header | The paragraph explaining what an MCP server is | Hover the "MCP" link in the one-line header; `docs/connectors.md` |
| Library → Connectors → Install form | "Its tools ask before they run until you say otherwise." | The toast after installing states the actual mode; the header line above the form states it too |
| Connector catalog cards | "Runs here", "No sign-in" and the category chips | Category: the filter row above the grid. Transport and sign-in: still shown whenever they are not the default (Remote, API key, Browser sign-in, Needs settings) |
| Today → Health card | Nine "—" rows while nothing is logged | "View all" on the card (unchanged) opens Health with every metric; rows reappear as soon as one is logged |
| Sidebar | "0" badges on Lists and on empty Spaces | The badge returns at 1; the count also appears in the row's hover title (Spaces) and on the Lists page header |

## What was not changed, and why

- **Approval cards, the mail review card, permission mode and Allow all domains.** Out of scope by the brief; their
  density is the safety contract made visible.
- **Tool-call folding** (C6), the **desk strip** status line, and the **Memory settings pane** (P4) are each a
  recent design (PRs #59, #62, #69) that deserves its own revisit with usage behind it.
- **Toolbars with many icon buttons** (Calendar, Files editor, Mail reader footer) all have titles and accessible
  names; collapsing them into overflow menus would trade one click for two on the most-used actions.

## Verification

- `npm run typecheck`, `npm test` (957 pass), the e2e suites touching changed surfaces, and the backend suite were
  run one at a time; results are in the PR description.
- After screenshots for every changed surface are in `docs/ux-shots/after/` with the same names as their
  `before/` counterparts.
