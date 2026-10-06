# UI/UX review (report only)

Date: 2026-10-05. Base: `origin/main` at 607a6bc7. Branch: `worktree-uiux-review`. This file and the
screenshots under `docs/research/uiux-review/` are the only additions; no code changes, no tests, no builds.

## Scope and method

- Method: the gstack design-review audit phases (first impression, design-system extraction, page audit
  against its 10-category checklist, trunk test, cross-page consistency), run as a read of the renderer
  code (`src/renderer/src`, 64K lines of TS/TSX/CSS) plus the one live screen that was visible. The fix loop
  was skipped on purpose.
- Three analysts each took one area (navigation, IA, onboarding and shortcuts; visual consistency and
  empty/loading/error states; accessibility, focus, contrast and motion) and a fourth took feature overlap.
  Every item below that carries a line number was re-checked by the orchestrator; contrast ratios were
  recomputed from the tokens in `styles.css`.
- Live app: Nate's `electron-vite dev` instance from the main checkout at 7d177eb9 (2026-10-04), which is
  153 commits behind the reviewed code. It was captured with `screencapture -l <window>` and never driven:
  the memory notes record a 2026-09-30 incident where synthetic clicks against the live data directory
  deleted two conversations, so navigation was not attempted. The window was 900x1410, light theme, Files
  view with a note in split mode and the page agent (⌘I) open. Everything merged since (read-mode docs,
  comments, attention dots, approval history, teach-a-task, PDF download, the Permissions tab) was reviewed
  from code only. Findings marked **code-only** have no screenshot.
- The prior audit `docs/ux-audit-2026-10-04.md` and the streamline audit on `origin/worktree-streamline-audit`
  (PR #14) were read for overlap; see the last two sections.

Screenshots (all from the older running build):

| File | What it shows |
|---|---|
| `uiux-review/live-doc-ask-panel.jpg` | Full window: Files view, note in split mode, page agent panel on the right |
| `uiux-review/live-doc-toolbars.jpg` | The four stacked header rows above a note (title bar, doc header, mode/actions row, format bar) |
| `uiux-review/live-side-chat-footer.jpg` | The page agent's composer with "Skipping permissions" and "Plan" in its footer |

## Executive summary

Grain's bones are good: one colour-token set with full light/dark parity, a global focus ring, a modal
hook that handles dialog roles, focus traps and Escape, menus with arrow keys, reduced-motion handled
globally, toasts that are live regions, and designed empty states on the big views. The problems are of
three kinds.

1. **Too many ways in, with no single trunk.** Views live in a sidebar, a title-bar icon strip, a Spaces
   bar, a dock, a command palette and ⌘-digit chords, and Settings → Modules lets each view move between
   the first three or vanish. The palette (⌘K) only knows titles of the last 8 chats and 8 files, no
   spaces, projects, jobs or actions, and ⌘K is also the link chord in the note editor. There are three
   unrelated search boxes. Chats, Memory and Projects have no nav row; Library and Health have no chord.
2. **Permissions and autonomy leak into every chat.** The composer footer shows Private, Skip
   permissions, Plan mode, Autonomy and Working folder on every chat, including the 380px page-agent
   panel (see `live-side-chat-footer.jpg`, where "Skipping permissions" is the loudest thing on screen).
   Settings stacks nine permission sections on one tab and then a separate Autonomy tab that says "what
   they may do is under Permissions". The approval card can still add its own rule buttons.
3. **No type, spacing or radius scale, and the small end is too small.** 20 distinct px font sizes
   (12px, 11px, 13px, 12.5px, 11.5px, 10.5px, 10px...), ~262 of ~570 size declarations under 12px, 15
   radii, 31 gap values, 232 padding shorthands, zero spacing or type tokens. Several token pairings miss
   WCAG AA: white on the pastel accent in dark theme is 1.76:1 (the month view's "today" number and the
   unread badge), white on the default sage button is 3.77:1, faint text on the composer is 4.38:1, and
   the light theme's default link/accent text is 4.30:1.

Plus two accessibility gaps that are not polish: calendar events and canvas windows are mouse-only (no
tabindex, no key handler, drag to create/move/resize), and the chat transcript has no live region.

The cheapest high-value work is a day of token fixes (five colour values, a type floor, chord hints on
nav rows, the clipped segmented control, a Quit button on the failure screen). The redesigns that matter
are one global palette/search, one "Mode" menu replacing four footer toggles, and a permissions tab split
into "what it may do" and "how it works". The cut/merge list agrees with the streamline audit on the
duplicate plan card, the two todo mirrors, sticky notes vs doc widgets, and Meetings vs doc recordings,
and adds the Integrations/Connectors split, the four schedulers, the title-bar app strip and the
`navPlacement` mechanism.

## First impression (live screen, `live-doc-ask-panel.jpg`)

I'm looking at a 900px window. My eye goes first to the big black "Ask about this page" header on the
right, then to the "20" badge in the title bar, then to the pill that says "Skipping permissions" at the
bottom right. None of those three is the note I had open. The note's title sits on the second of four
header rows; above it a "Not" segmented control has been clipped (it should read "Notes"), to its right a
"+ New" split button and five unlabeled icons. Below the title: an edit/split/preview segment, five more
icons, a "Record" split button, a download icon, a panel icon. Then a format bar with eleven controls.
Only then the text, which in split mode is stacked editor-over-preview, so the rendered diagram in the
lower half is a thumbnail nobody can read. The status line at the bottom is useful (line count, words,
read time) and ends with a `⌘⇧B bold…` hint in 11px that is the only on-screen shortcut hint in the app.

The page communicates **capability**: a lot can be done here. In one word: **crowded**. Page-area test:
the title bar and the format bar are nameable in two seconds; the third row (mode + five icons + Record +
two icons) is not, and the right panel's footer ("Skipping permissions", "Plan") is a settings strip that
does not belong to a question box.

Trunk test on this screen: what app (no logo while the sidebar is collapsed; the window title says it),
what page (the note title, yes), what sections (sidebar collapsed, so only the four title-bar icons:
fail), options at this level (yes, too many), where am I (project chip "Robotics", yes), how to search (no
search visible anywhere: fail). PARTIAL.

## Quick wins

Each item: screen, problem with evidence, screenshot, suggested change, effort, impact.

1. **Dark-theme white-on-pastel-accent.** Screen: Calendar month view, chat "jump to latest" badge.
   Problem: `.jump-count` and `.cal-month-cell.today .dom` set `background: var(--accent); color: #fff`
   (`styles.css:643`, `:2218`); in dark theme `--accent` is the pastel `#b5c9b0`, so the ratio is 1.76:1.
   Code-only. Change: use `--accent-solid` + `--on-accent`, as `.cal-dayhead.today` already does
   (`:2120`). Effort S. Impact high.
2. **Default accent button fails AA in dark theme.** Screen: every `.primary-btn`, `.send`, avatar, day
   header. Problem: `--on-accent` white on dark `--accent-solid` is 3.77:1 (sage), 3.69 (mint), 4.25
   (sky); lilac, rose and fog pass (`styles.css:37,128-136`). Code-only. Change: darken those three solids
   about 8% (sage `#6e8b6a` → about `#5f7c5b`). Effort S. Impact high.
3. **Faint and danger text under 4.5:1.** Screen: composer placeholder, user bubbles, error text in
   modals. Problem: dark `--text-faint #8f8d86` on `--bg-input` is 4.38 and on `--user-bubble` 4.13; dark
   `--danger #e5484d` on `--bg-elev` 4.06, on `--bg-input` 3.71; light `--accent #5a7d5c` as link text on
   bg is 4.30 and 3.80 on the bubble (`styles.css:32,50,73`). `--text-faint` has 242 uses, mostly at
   10-11px. Code-only. Change: dark faint → about `#9a9890`; add a dark `--danger-text` near `#ff6b70`;
   light sage accent → about `#4f7152`. Effort S. Impact med-high.
4. **Clipped "Notes" segmented control at narrow widths.** Screen: Files header. Problem: at 900px the
   Notes/Uploads segment renders as "Not" (`live-doc-toolbars.jpg`, top left); the header row has no
   min-width or overflow rule for the segment while the AppSwitcher and "+ New" keep their size
   (`DocsView.tsx:331-345`). Change: let the segment shrink to icons or collapse the AppSwitcher first.
   Effort S. Impact med.
5. **Show the chord on nav rows and app icons.** Screen: sidebar, title bar. Problem: only ⌘N and ⌘,
   appear in the sidebar (`Sidebar.tsx:268,466`); AppSwitcher tooltips use `navTitle`, label plus
   description, no chord (`shell/nav.tsx:48`). ⌘0..⌘7 exist for eight views but are shown nowhere but the
   Help overlay and the menu. Code-only. Change: append the chord to `navTitle` and to the sidebar row's
   trailing slot. Effort S. Impact med.
6. **⌘B and ⇧⌘M are dead inside the note editor.** Screen: Files editor. Problem: the menu handler
   re-dispatches ⌘B and ⇧⌘M into the focused `.md-input` (`store.ts:1066-1067`) so the editor can take
   them, but the editor only binds ⇧⌘B (bold) and ⌃⌘M (maths) (`MarkdownEditor.tsx:554,566`,
   `shared/shortcuts.ts:75,78`), so both chords do nothing while editing. ⌘K becomes "link", so the palette
   is unreachable from a note. Code-only. Change: bind plain ⌘B to bold when the editor has focus (and
   move Toggle Sidebar to ⌘\), drop the ⇧⌘M re-dispatch, and give the palette a second chord (⇧⌘K) or let
   ⌘K open it when nothing is selected. Effort S. Impact med.
7. **Empty chat after onboarding is a bare greeting.** Screen: new chat. Problem: `<h1>{greeting()}</h1>`
   and nothing else once `firstPrompts` is cleared (`ChatView.tsx:51,131-141`); the page-agent panel does
   show hints (`PageAgentPanel.tsx:73`). Code-only. Change: always show three starter chips and a one-line
   "`/` for commands, `@` for agents and files" hint. Effort S. Impact med.
8. **Zero-space state is blank.** Screen: Spaces with no spaces. Problem: the plane renders nothing when
   `canvas` is null (`canvas/Canvas.tsx:515`), Overview has no empty branch, and the sidebar row says only
   "No spaces yet." (`SidebarSpaces.tsx:203`). The windows-but-empty intro is good. Code-only. Change:
   reuse the intro with a "Create a space" button when `order.length === 0`; put "New space" on the sidebar
   row. Effort S. Impact med.
9. **Backend-failed screen has no Quit.** Screen: `BackendFailed`. Problem: copy says "quit and reopen
   Grain" but offers only Try again, Copy details, Open logs (`BackendFailed.tsx`). Code-only. Change: add
   Quit. Effort S. Impact low.
10. **Boot screen has no timeout copy.** Screen: launch. Problem: a lone logo with `role="status"` while
    `!ready` (`App.tsx:233-239`); if `init()` hangs without an error the user sees the logo forever (the
    request timeouts in `lib/api.ts` make this unlikely but not impossible). Code-only. Change: after 10s
    show "Taking longer than usual" with Open logs. Effort S. Impact low-med.
11. **"Loading…" styled as an empty state.** Screen: Health, Home cards, Crew widget, Backlinks. Problem:
    `HealthView.tsx:110` renders "Loading…" in `.empty-hint.big` (60px padding) so a load looks like
    nothing-to-show; `Backlinks.tsx:23` says "Loading" without the ellipsis; `BackendBanner` says "stopped
    and is restarting…" even in the `starting` state (`BackendStatus.tsx:9`). 28 plain "Loading…" strings,
    20 spinners, 0 skeletons. Code-only. Change: one `<Loading>` (150ms delay, aria-live) and fix the two
    strings now; skeletons are a redesign item. Effort S. Impact med.
12. **Deleting a saved todo filter removes it locally even when the delete failed.** Screen: Lists.
    Problem: `dropFilter` swallows the API error and filters the row out anyway (`TodosView.tsx:53`); the
    filters and lists loads also fail silently (`:47,61`). Code-only. Change: toast and keep the row on
    failure. Effort S. Impact med.
13. **Hit targets under 24px.** Screen: canvas window close (16x16, `styles/canvas.css:357-366`),
    `.cite-chip` (16px tall, visible in `live-doc-ask-panel.jpg` as the small "5" and "1" chips), convo-row
    icon buttons (22px, `styles.css:346`), 6-7px resize handles. Change: the `::before { inset: -4px }`
    trick `.icon-btn.xs` already uses. Effort S. Impact med.
14. **Unlabeled form controls.** Screen: space rename (`canvas/SpacesBar.tsx:107`), chat widget input
    (`canvas/widgets/chat.tsx:141`), todos widget (`canvas/widgets/todos.tsx:124-132`), calendar
    (`CalendarWeek.tsx:443`), plan card (`ActionPlanCard.tsx:126`), inbox (`AgentInbox.tsx:728,730`),
    Activity (`ActivityView.tsx:255,286,430`). About 56 of 403 controls have no label, id or aria-label;
    81 icon buttons have `title` but no `aria-label`. Code-only. Change: add `aria-label`; a codemod can
    mirror `title` into `aria-label`. Effort S. Impact med.
15. **Command palette is not a combobox.** Screen: ⌘K. Problem: the input has `aria-controls` and
    `aria-activedescendant` but no `role="combobox"`, `aria-expanded` or `aria-autocomplete`, and is
    named only by placeholder (`CommandPalette.tsx:67-76`). Code-only. Change: add the three attributes
    and an `aria-label`. Effort S. Impact low-med.
16. **Chat transcript has no live region.** Screen: chat. Problem: `.messages` has no `role="log"` or
    `aria-live` (`ChatView.tsx:129`); only the run-status line is `role="status"` (`Message.tsx:235,247`).
    Code-only. Change: `role="log" aria-live="polite" aria-relevant="additions"` on the list, or a
    visually hidden "Reply complete" status. Effort S. Impact med for screen-reader users.
17. **Chord collisions.** Screen: shortcuts. Problem: ⌃1..9 for spaces is Mission Control's desktop
    switch when several desktops exist; ⌥Space (quick ask) inserts a non-breaking space in text fields
    and is a common launcher default; ⌘0 is Today while ⌘⌥0 is zoom reset, the reverse of the platform
    convention (`shared/shortcuts.ts:37,48,89,111`). Code-only. Change: spaces to ⌥1..9, quick ask to
    ⌃⌥Space by default, ⌘0 to zoom reset and Today to ⌘1 with the rest shifted. Effort S. Impact med.
18. **Stray hard-coded colours.** `#adbac7`, `#c0392b`, `#111`, `#bbb` outside `print.css` break theme
    parity (28 distinct hex literals outside the token blocks, most of them legitimately in print). Change:
    replace with tokens. Effort S. Impact low.
19. **Opacity-dimmed content under 3:1.** `.kcard.done` 0.6, `.cal-month-cell.dim` 0.55,
    `.section-row .ghost` 0.6 put muted text at 2.4-3.1:1; done todos and other-month days are still
    content. Change: floor at 0.7 or use a dedicated muted colour. Effort S. Impact low.
20. **Palette "New chat" leaves the space.** Screen: ⌘K inside Spaces. Problem: the palette calls
    `newChat(null)` which switches to the chat view (`CommandPalette.tsx:25`, `store.ts:2248`), while the
    sidebar button and ⌘N open a chat window in the space (`Sidebar.tsx:267`, `store.ts:1074`). Code-only.
    Change: route through the same canvas-aware helper. Effort S. Impact med.

## Bigger redesigns

1. **One global search and palette.** Screens: ⌘K, sidebar search, ⌘⇧F, ⌘F. Problem: three unrelated
   searches. The sidebar field filters chat titles with a "needs you" bell beside it
   (`Sidebar.tsx:303-330`); ⌘⇧F focuses that field, which is hidden when the sidebar is collapsed; ⌘F is
   the in-chat find bar (`ChatView.tsx:128`); the palette lists views, "new chat", "new file", help, the
   8 newest chats and files by title, and the Settings tabs (`CommandPalette.tsx:15,27-35`). No message
   bodies, spaces, projects, jobs, todos, mail or calendar, and no verbs (lock space, plan mode, theme,
   pop out). Code-only. Change: ⌘K becomes the trunk: typed text over 2 characters queries the chat
   search the sidebar already uses plus docs, todos and events; add Spaces, Projects, Jobs and a short verb
   list; rename the sidebar field "Filter chats". Effort M. Impact high.
2. **One "Mode" control instead of four footer toggles.** Screen: composer footer, page-agent panel,
   chat widgets. Problem: the footer carries Private, Skip permissions, Plan mode, Autonomy and Working
   folder (`Composer.tsx:446-466`), on top of model/effort and Style selects (`ChatControls.tsx:33-46`),
   attach, mic, voice and send; 11+ controls in a full chat and the same strip in a 300-380px panel
   (`live-side-chat-footer.jpg`: "Skipping permissions" and "Plan" under a one-line question box).
   "Skipping permissions" is one click from any chat and its tooltip is a paragraph
   (`SkipPermissionsToggle.tsx:38`). Change: keep model, attach, mic and send visible; one "Mode" menu
   with Ask / Plan first / Autonomous / Skip asks, a dot when non-default, and a first-time confirmation on
   Skip; Private, Style and Working folder into a "Chat options" popover; merge mic and voice chat into
   one control. Effort M. Impact high.
3. **Split Permissions into "what it may do" and "how it works", fold Autonomy in.** Screen: Settings.
   Problem: the Permissions tab stacks Tool access, Always ask, Rules, Grants, Run safety, Shell and
   sandbox network, Browser, Desks, Skip permissions, File edit mode and Plan mode default
   (`SettingsModal.tsx:572-613`); the Autonomy tab holds limits for the same autonomous chats and points
   back to Permissions (`:617`); approval cards add inline rule buttons (`ApprovalRules`). Plan default
   and edit mode are not permissions. Code-only. Change: two tabs, "Permissions" (tools, always-ask,
   rules, grants, browser, shell) and "Working style" (plan default, edit mode, run safety, autonomy
   limits, desks); every standing grant listed with revoke in one place, as the prior audit asked.
   Effort L. Impact high.
4. **Type, spacing and radius tokens.** Screen: everywhere. Problem: `font-size` 666 hard-coded px
   declarations, 2 `var()`; 20 distinct px sizes with half-pixel steps (12.5 x40, 11.5 x37, 10.5 x28) and
   about 262 declarations under 12px including 9px badges (`styles.css:431,2565`, `styles/docs.css:187`,
   `styles/widgets.css:56`); 15 distinct radii, 31 gap values, 232 padding shorthands; all px, no rem, so
   OS text size is ignored and the 80-160% zoom in `main/index.ts:366` is the only scaling. Code-only.
   Change: `--fs-xs 11 / sm 12 / md 13 / lg 15 / xl 18`, `--sp-1..6` on a 4px base, `--r-sm/md/lg/pill`;
   codemod the half-pixel sizes to the nearest step, floor captions at 11px, raise zoom cap to 200%.
   Effort M. Impact high.
5. **Keyboard-operable calendar and canvas.** Screens: Calendar week, Spaces. Problem: `div.cal-event`
   has only `onMouseDown`/`onClick` (`CalendarWeek.tsx:429-437`); all-day and todo chips are
   `div onClick` (`:368,371`); create is drag-select, move/resize drag-only. Canvas `.win` has no tabindex
   or role (`canvas/WindowFrame.tsx:149`); window options are `onContextMenu` only (`:136`); move and
   the 8 resize handles are pointer-only divs (`:184-185`); close is a real button at opacity 0 until
   hover or focus-visible (`styles/canvas.css:372-376`) but cannot be reached because the window itself
   is not focusable. 19 further `div onClick` surfaces with no role or key handler (Overview, SpacesBar
   tabs, DocumentsView cards, MemoryView and StyleView paragraphs, note widget). Code-only. Change:
   events and chips as `<button>` or `role=button tabIndex=0`; a "New event" button; Alt+arrows nudge for
   move and Alt+Shift+arrows for resize on both calendar events and windows; `.win` focusable with
   Shift+F10 opening the existing menu; roles on the 19 divs the way `ChatRow.tsx:82-85` and
   `DocTree.tsx:435` already do. Effort M-L. Impact high.
6. **One EmptyState, one Chip, one Tabs.** Screen: everywhere. Problem: `.empty-state` (designed, with
   action) coexists with 10+ bespoke `*-empty` classes and a faint `.empty-hint` family with no action
   (Sidebar, Activity, Graph, DeskFiles, DocTree, Meetings list); about 50 chip/pill/badge/tag class
   names restyled per feature (`.act-pill`, `.desk-pill`, `.cal-chip`, `.cite-chip`, `.mc-badge`...);
   three tab idioms (`.tabs` with `role=tablist` in Library, `.seg` with `role=tab` in Files, a third in
   `DeskPanel.tsx:20`; active class `active` vs `on`). Code-only. Change: `<EmptyState icon title body
   action compact>`, `.chip[data-tone][data-size]`, `<Tabs>` and `<Segmented>`. Effort M-L. Impact
   med-high.
7. **Loading and error states as components.** Problem: 0 skeletons; `HomeView.tsx:265` "Could not
   load." with no retry; `MailView.tsx:256` pipes raw `"500 Internal Server Error"` or
   `JSON.stringify(detail)` from `lib/api.ts:102-110` into a red `.notice-bar.error` with no action;
   about 106 swallowed catches (`WorkflowsPanel` x5, `SettingsModal` x5, `PopoutSurface` x4); 384
   `toast(` calls, the error ones mostly "Jobs: <message>". Contrast with the good ones: `BackendFailed`
   ("Your data is safe…" plus three actions), `lib/errorAction.ts` (per-kind Retry / Open Settings / Pick
   a model / Compact and retry), `RenderBoundary`. Code-only. Change: `humanize(e)` in `apiError.ts`,
   `<ErrorNotice retry>` replacing bare notice bars, error toasts carry a Retry, list skeletons for Mail,
   Lists, Today cards and Meetings. Effort M. Impact high.
8. **Files header: four rows of chrome.** Screen: Files with a note open (`live-doc-toolbars.jpg`).
   Problem: title bar (tree toggle, Notes/Uploads, + New, app icons), doc header (title, project, folder,
   Saved), mode row (edit/split/preview, five icons, Record, Export, side panel), and an always-on
   11-button FormatBar (`DocsView.tsx:331-410`, `features/notes/FormatBar.tsx`); about 170px before the
   first line of text in a 900px window, and split mode stacks editor over preview so the preview is a
   thumbnail. Change: fold Record and Export into a "…" menu, move edit/split/preview into the View menu,
   show the FormatBar only while the editor has focus (a `SelectionToolbar` already exists), and make
   split side-by-side above about 1100px only. Effort M. Impact med-high.
9. **Onboarding: seven steps to the first message.** Screen: first run. Problem: Welcome, Provider,
   Key, Test, Google, About, Done (`components/onboarding/steps.ts:3-4`), modal over the whole app until
   Test passes (`steps.ts:70`); "Set up later" exists (`Onboarding.tsx:276`) but no local or offline path
   is offered up front; Finish lands in an empty chat with three chips that vanish after that session
   (`Onboarding.tsx:104-108`, `steps.ts:114-121`). Code-only. Change: Provider + Key + Test on one screen
   that tests on paste; Google and About merged into "Connect something" with Skip; keep the chips.
   Effort M. Impact med.
10. **Settings save model.** Screen: Settings. Problem: Integrations says "Signing in and the sync
    switches take effect at once; the rest is saved with Save" (`SettingsModal.tsx:499`); Meetings is
    immediate; the other tabs are drafts with a footer that changes per tab (`:36-60,335`); about 76
    controls in the modal, Memory/Data/Integrations nest whole feature panels (`McpSettings` 564 lines).
    Code-only. Change: save on change everywhere, keep a draft only for credentials, add a settings search
    (the palette already lists the tabs). Effort M. Impact med.
11. **Activity is a 1,145-line page with its own design system.** Screen: Activity. Problem: 56
    controls, hand-rolled `.act-*` pills, tags and sections, 9-10.5px text, about 8 empty-hint variants
    (`ActivityView.tsx`). The streamline audit flags the same file (its 3.8). Code-only. Change: tabbed
    panels on the shared chip and empty-state components. Effort M-L. Impact med.
12. **Memory needs a home.** Screen: nav. Problem: Memory is a product pillar but ⌘6 opens a Settings
    tab (`store.ts:2110-2113`) and the palette files it under "Settings" (`CommandPalette.tsx:35`); Google
    sign-in is under Settings → Integrations while MCP connectors live in Library → Connectors
    (`SettingsModal.tsx:497-520`, `LibraryView.tsx:69`). Code-only. Change: Memory as a Library tab or a
    sidebar row; Integrations and Connectors as one "Connections" page. Effort M. Impact high.

## Things to cut or merge

Ranked by user value over effort. "SA" cites the streamline audit section that reaches the same
conclusion from the code-size side; "UX-10-04" cites the prior audit's backlog item.

1. **One plan-approval card.** What: `PlanApproval.tsx` (103) + `lib/planSteps.ts` (92), mounted from
   `ToolEvents.tsx:343` for a pending `propose_plan`; `ActionPlanCard.tsx` (269) + `lib/planDigest.ts`
   (167), mounted from `DeskPlan.tsx:45`. Problem: the same approve/edit/drop-steps card in two styles, and
   the two canonicalisers sort differently although the digest binds the approval to its arguments (SA
   2.2). Screenshot: code-only. Change: keep `ActionPlanCard` + `planDigest` with a compact layout for the
   chat; delete the other pair. Effort M. Impact high.
2. **Three nouns for agent work: Chat, Task, Skill.** What: the user currently meets subagents
   (`SubagentPanel` 93), "Work autonomously" desks (`AutonomyToggle`, `DeskStrip`, `DeskPanel`,
   `DeskFiles`, `DeskReview`, `DeskChanges`, `DeskBrowser`, `DeskPlan`, `DeskApprovalCard`,
   `CoworkSettings`, about 1,500 lines), scheduled jobs (`AgentInbox` 950, including job CRUD),
   routines (`AgentHome` tab), shell jobs (`ShellJobs`), workflows (`WorkflowsPanel` 253), commands
   (`DefsPanels` 64), skills (`SkillsPanel` 365 + `TeachTaskPanel` 203), agents (`AgentsPanel` 178), crew
   (`CrewRing` + widget, 330), research (`ResearchTrail`). "Job" alone means a scheduled run, a routine
   and a running shell process. Change: *Chat* for one-off work (subagents are helpers inside it); *Task*
   for anything that runs on its own (scheduled run, autonomous session, routine) in one Tasks list;
   *Skill* for anything reusable (skills, commands, workflows) with a kind badge; "Agent" becomes a
   persona on a chat or task, not a fourth shelf. Rename "Shell jobs" to "Running commands". Effort M
   (mostly names and tabs). Impact high.
3. **The word "plan" means six things.** What: `PlanPanel` (the `todo_write` checklist), `PlanModeToggle`
   ("Plan mode", 7 uses), the `propose_plan` card, `DeskPlan`, autonomy "Plan first", and "Day plan" /
   "Plan my day" / "Plan my week" (7 uses). Change: checklist → "Steps"; one approval concept "Plan
   first"; scheduling → "Schedule my day/week"; the plan-mode toggle folds into the Mode menu (redesign
   2). Effort S. Impact med-high.
4. **Merge Commands and Workflows into Skills.** What: Library → Automations holds Workflows and
   Commands beside a Skills tab (`LibraryView.tsx:12-17`), and the composer lists `/skill`, `/commands`
   and `/skills`. Change: one list with a kind badge; workflows' waves and approved steps stay as an
   "advanced" kind. Effort M. Impact med.
5. **One scheduler surface.** What: jobs are created from the Today inbox (`AgentInbox`), per-agent
   Routines (`AgentHome`), Teach a task's Schedule (`TeachTaskPanel`), `/schedule` and `/loop`; the full
   list lives only on Today when that card is on. Change: one Tasks page (or Library tab) listing every
   job; Today keeps the "needs you" slice; split job CRUD out of the approvals queue. Effort M. Impact
   med-high.
6. **Four schedulers of the day.** What: `PlannerPanel` (87) mounted on Today, Lists and Calendar
   (`HomeView.tsx:414`, `TodosView.tsx:216`, `CalendarView.tsx:231`) with the button reading "Plan my day"
   or "Plan my week" by location; backend `planner.py` (230) reimplements `scheduling.py` (432) (SA 2.4);
   work-hours settings sit under Integrations (`PlannerMailSettings`). UX-10-04 item 7, still partial.
   Change: one entry on Calendar ("Schedule my week") ending in a `calendar_propose` card; Today shows only
   existing proposals; work hours move next to Calendar. Effort M. Impact med.
7. **Meetings as a Files mode.** What: `MeetingsView` (544) + `MeetingSettings` (406) + the Today
   Meetings card, versus in-doc recording in `features/docrec` (2,159) which reuses the same recorder
   (`docs/docs-editor.md:202-207`); two transcript/summary UIs and two revision systems (SA 2.10).
   "Record" lives in a sidebar view, a Today card and every file. Change: keep the Files recorder; Meetings
   becomes a filtered list of recorded docs (a Files tab); the Settings → Meetings tab content moves to
   that page's gear. Effort L. Impact med.
8. **Integrations and Connectors are one thing.** What: Google and Microsoft sign-in under Settings →
   Integrations (`SettingsModal.tsx:497-520`), MCP servers under Library → Connectors
   (`LibraryView.tsx:69`, `McpSettings` 564). Change: one Connections page listing accounts and servers
   with the same row shape. Effort M. Impact med-high.
9. **Memory in ten places.** What: Settings → Memory (`MemoryPanel`: List, Graph, Voice), project
   Memory tab, Today "Recently learned", the sidebar tidy badge (`Sidebar.tsx:462`), Memory and Graph
   widgets (74 + 48), agent memory notes, `MemoryChips`, `ContextDrawer` toggles, Behavior's system
   prompt, project Instructions. "Voice" (`StyleView` 202) is a writing-style setting filed under Memory.
   Change: one Memory page (list + graph) reachable from the nav; Voice moves to Behavior beside response
   style; drop the standalone Graph widget. Effort S-M. Impact med.
10. **Per-chat source toggles duplicate Settings.** What: `ContextDrawer` (315) lists per-chat switches
    for memory, graph, docs, skills, web, learning, writing style, meetings and activity plus tool
    overrides, trace and shell jobs; "Private", "Don't learn from this chat" and "use memory in this chat"
    are three controls over one idea. Change: one Private switch in the composer, one Sources list in the
    drawer showing only what differs from the default. Effort M. Impact med.
11. **Cut the redundant canvas widgets.** What: 14 kinds (`shared/types.ts:2006-2008`): chat, todos,
    calendar, note, memory, graph, documents, recap, project, usage, activity, doc, face, crew. `usage`
    mirrors Settings' usage chart, `recap` the Today recap, `activity` the Activity view, `documents`
    Files → Uploads (SA 2.12), `note` is a second document store with no history (SA 2.1, ~380 lines) next
    to `doc`. Mail and Health have no widget, so the set reads as arbitrary. Change: keep chat, doc (note
    as a colour mode), todos, calendar, project, crew, face; cut usage, recap, activity, documents, graph,
    memory. Effort S. Impact low-med.
12. **Drop per-view nav placement.** What: `navPlacement` and `hiddenViews` let each view sit in the
    sidebar, the title bar or nowhere (`shell/nav.tsx:48`, `SettingsModal.tsx:657-685`), with "More
    modules…" as the only hint (`Sidebar.tsx:276`); hiding a view silently breaks its chord with a toast
    (`store.ts:1095-1096`). It is a customisation UI for a four-item problem, and the backend registers
    modules (`mailwatch`, `planner`) the shell does not (SA 2.17). Change: sidebar for hubs, title bar for
    Lists / Calendar / Mail / Health fixed, ⌘K for everything, one hide toggle per view. Effort S. Impact
    low-med.
13. **Settings 9 → 7 tabs.** What: Permissions + Autonomy (redesign 3); Modules into Behavior
    (Appearance and layout); the Daily digest toggle sits under Meetings (`SettingsModal.tsx:549-566`)
    although it covers review notes, app time and permissions. Change: digest → Behavior →
    Notifications; Meetings tab → a card on the Meetings page. Effort S-M. Impact med.
14. **Two "inboxes" on Today.** What: the Agent inbox (approvals, proposals, digest) and the Mail inbox
    card share the word; Modules had to relabel one "Mail inbox" (`modules.ts:30`). Change: rename the
    agent one "Needs you". Effort S. Impact low-med.
15. **Uploads is still a second home for documents.** What: Uploads as a Files tab (`DocsView.tsx:35`),
    a project Uploads tab (`ProjectView.tsx:139`), a widget, desk outputs and the Drive card. UX-10-04
    "One Files" mostly landed (Library → Made is gone). Change: one list with a type filter (Note /
    Upload), keeping read-only uploads as a type, not a tab. Effort M. Impact med.
16. **Health and Activity off by default.** What: `features/health` 912 lines in the title bar plus a
    Today card; `ActivityView` 1,145 lines in the sidebar plus a widget; both now ship on
    (`moduleToggles.ts:9` `DEFAULT_HIDDEN_VIEWS = []`). Activity's "habits to automate" overlaps the Tasks
    list. Change: ship off, reachable by ⌘K and Modules; route Activity's suggestions into Tasks. Effort
    S. Impact med.

Keep as is (looks duplicated, is not): Notes vs Uploads as *types* (edited vs read-only); Skills vs
Agents (procedure vs persona, already linked in `AgentHome`); the plan checklist vs the `propose_plan`
gate (working state vs approval, only the labels clash); Todos vs Google Tasks with sync on (already
gated: tools hidden, card off); the palette, which is the only way to hidden views and Settings tabs.

## Overlap with the streamline audit (`origin/worktree-streamline-audit`, PR #14)

The streamline audit measured dead code and structure; this review measured what the user sees. Where
both looked at the same thing:

| Topic | Streamline audit | This review |
|---|---|---|
| Two plan-approval cards | 2.2: ~250 lines, two canonicalisers that sort differently | Cut/merge item: one card, two layouts; the UI inconsistency is the user-visible symptom |
| Sticky notes on spaces | 2.1: ~380 lines, second document store | Cut/merge item: the `doc` widget covers it |
| Two Google mirrors of todos | 2.9: ~675 lines | Cut/merge item: duplicate items when sync is on; UX-10-04 item 8 |
| `planner.py` vs `scheduling.py` | 2.4: ~100 lines of reimplementation | Cut/merge item: four "plan my day/week" entry points; UX-10-04 item 7 |
| Meetings view vs doc recordings | 2.10: two transcript/summary UIs | Cut/merge item: one recording UI, Meetings becomes a list of recordings |
| Two uploads lists | 2.12: `DocumentsView` vs `canvas/widgets/documents.tsx` | Folded into the EmptyState/Chip/Tabs redesign and "One Files" |
| `ActivityView.tsx` 1,145 lines | 3.8 refactor candidate | Redesign item 11 |
| `styles.css` 2,958 lines | 3.6 refactor candidate | Redesign item 4 (tokens) is the precondition for splitting it |
| Vocabulary drift ("canvas" vs "Space", two "workspace"s) | 2.18 | Nav finding: "Lists" vs todos, "File" vs "note", "Today" vs home |
| Backend modules vs shell registry | 2.17: mailwatch and planner have Today cards but no module | Cut/merge: the module/navPlacement mechanism itself |
| Settings toggles all read | 2.16: every toggle has a reader | Not a dead-code problem; the problem is where they sit (redesign items 3 and 10) |

Not covered by the streamline audit and new here: the title-bar app strip, the three searches, the
Integrations/Connectors split, the Memory tab, the composer footer, contrast and keyboard operability.

## Prior audit (`docs/ux-audit-2026-10-04.md`) backlog status

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Permissions in one place | Partial | One store and one tab with standing grants and revoke (`GrantsPanel.tsx`, `docs/permissions.md`); but the tab stacks about 14 sub-sections (`SettingsModal.tsx:572-614`), three per-chat toggles remain in the composer (`Composer.tsx:460-463`), and the approval card still offers six buttons (`ApprovalRules.tsx:54-74`). |
| 2 | One Files | Mostly landed | Library → Made is gone (`LibraryView.tsx:12-17`); Files is Notes + Uploads (`DocsView.tsx:35`). Uploads is still a tab here and in projects (`ProjectView.tsx:139`). |
| 3 | Settings 12 → 8 tabs | Landed (9) | `SettingsModal.tsx:41-62`; the Spaces tab is gone. |
| 4 | Command palette | Landed, thin | `CommandPalette.tsx` on ⌘K with an editor handoff (`main/index.ts:250`); no Library, Agents, Projects, Spaces, Jobs or content search. |
| 5 | Desk outputs beyond docs | Partial | `DeskReview.tsx:18-23`: New file, Append, Upload, Lists, Gmail draft, Download; no calendar event or widget. |
| 6 | Workflow runs report back | Landed | `workflows.py:482-491`. |
| 7 | Planner duplication | Partial | One `PlannerPanel` mounted three times; `schedule_suggest` is gone; `planner.py` and `scheduling.py` both remain (SA 2.4). |
| 8 | Google Tasks duplication | Landed | `tools.py:804` hides `google_tasks_*` while sync is on; the card shows only when sync is off (`HomeView.tsx:417`); sign-in copy discloses the sync (`GoogleSettings.tsx:102`). |
| 9 | Delete orphans | Mostly landed | `/planner/config` and `/mail/watch/config` now have callers; `useCanvas.openChat` is referenced only by a test; the rest not re-audited. |
| 10 | Canvas onboarding | Landed, thin | One sentence in `Onboarding.tsx:155`; `docs/spaces.md` exists; the sidebar section still says only "No spaces yet." |

Also since that audit: the standalone Cowork view is folded into chats, and every view ships visible
(`hiddenViews: []`), which is why Meetings and Activity now count against the default nav.

## Appendix A: inferred design system

- Fonts: 2 families (`--font` system stack, `--mono`) plus `--ed-font` for the editor. Good.
- Colour: ~42 tokens on dark `:root`, 25 overridden for light and system-light, identical sets; five
  accents each with light/dark/system overrides; `color-scheme` set per theme (`styles.css:21,61,94`).
  Extra token sets in `styles/canvas.css:17` and `styles/widgets.css:10`; a chart palette at
  `styles.css:1683`. `var(--…)` used 2,303 times; 28 distinct hex literals outside the token blocks, most
  in `print.css`.
- Type: no tokens. 20 distinct px sizes; top values 12px x195, 11px x164, 13px x124, 12.5px x40, 11.5px
  x37, 10.5px x28, 10px x27, 14px x13, 15px x10. Body copy in chat and notes is 13-15px.
- Spacing: no tokens. Gap values 8px x149, 6px x149, 4px x76, 10px x52, 2px x46, 5px x37, 12px x24, 3px
  x22, 7px x18. 232 distinct padding shorthands.
- Radius: 15 distinct; 8px x88, 6px x84, 10px x62, 50% x44, 999px x23, 12px x18, 4px x17; about 16 uses
  of `var(--radius)`.
- Shared classes in TSX: `ghost-btn` 255, `icon-btn` 185, `primary-btn` 108, `tag` 37, `seg` 29, `chip`
  15, `modal` 11. One toast stack, one `.empty-state`.
- Motion: `prefers-reduced-motion` handled by a global `*` rule (`styles.css:2670`) plus 5 blocks; 13
  infinite animations all covered; no `transition: all`.
- Focus: `--focus-ring` per accent and theme (`styles.css:40,77,128-144`); one `!important` rule covers
  links, buttons, inputs, `[role=button]` and `[tabindex]` (`:2623-2632`); text fields use border + glow
  (`:2637-2644`). 68 `outline: 0/none`, nearly all on fields that ring another way.

## Appendix B: contrast table (WCAG ratio, computed from `styles.css` tokens)

| Foreground on | bg | elev | input | user bubble |
|---|---|---|---|---|
| Dark `--text` #ecebe8 | 14.7 | 13.3 | 12.2 | 11.5 |
| Dark `--text-muted` #9c9a94 | 6.24 | 5.65 | 5.17 | 4.88 |
| Dark `--text-faint` #8f8d86 | 5.29 | 4.79 | **4.38** | **4.13** |
| Dark `--danger` #e5484d | 4.49 | **4.06** | **3.71** | **3.51** |
| Light `--text-muted` #6b6963 | 5.08 | 5.49 | 5.49 | **4.49** |
| Light `--text-faint` #6f6d66 | 4.79 | 5.18 | 5.18 | **4.23** |
| Light `--accent` #5a7d5c (sage, as text) | **4.30** | 4.64 | 4.64 | **3.80** |
| Light `--tk-code` | 4.55 | 4.92 | 4.92 | **4.03** |

White `--on-accent` on dark `--accent-solid`: sage 3.77, mint 3.69, sky 4.25 (fail); lilac 4.77, rose
4.68, fog 4.61 (pass). White on dark pastel `--accent`: 1.76. White on dark `--danger`: 3.91. Borders:
`--border-strong` on bg 1.65 dark, 1.52 light (3:1 target for UI edges). `.send:disabled` at 0.35
opacity: 2.9 dark, 2.2 light (disabled is exempt).

## Appendix C: shortcut inventory (`src/shared/shortcuts.ts`)

| Chord | Action |
|---|---|
| ⌘, ⌘K ⌘/ or ? ⌘B ⌘I ⌃⌘I | Settings, palette, help, sidebar, page agent, context panel |
| ⌘⌥0 ⌘= ⌘- | Zoom reset, in, out |
| ⌘N ⇧⌘N ⇧⌘D ⌘U | New chat, new file, today's file, upload |
| ⌘0..⌘7 | Today, Chats, Lists, Calendar, Files, Mail, Memory (opens Settings), Activity |
| ⇧⌘M ⇧⌘[ ⇧⌘] | Meetings, previous chat, next chat |
| ⇧⌘F ⌘F ⌘G ⇧⌘G | Search chats (sidebar field), find in chat, next, previous |
| ↵ ⌘↵ ⇧↵ Esc ⇧⌘P | Send, steer, newline, stop, cycle plan mode |
| ⌘S ⇧⌘B ⇧⌘I ⇧⌘E ⌃⌘M ⌘K Tab ⌃⌥D | Note editor: save, bold, italic, code, maths, link, indent, dictation |
| ⇧⌘C ⌃⌘N ⌥⌘← ⌥⌘→ ⌥⌘↑ ⌃1..9 ⌃⌘T ⌃⌘L | Spaces: toggle, new, previous, next, overview, jump, tidy, lock |
| ⌘W ⌘M ⌃⌘O ⌃⇧⌘O ⌃⌘P ⌃⌘[ ⌃⌘] | Close, minimize, pop out, return, pin, opacity down/up |
| ⌥⌘G ⌥⌘F ⌃⌥⌘Space ⇧⌘Space ⌥Space | Gather widgets, pop-outs to front; global gather, quick capture, quick ask |

Gaps: Library, Health, Projects, Jobs and the Today inbox have no chord. Discoverability: Help overlay
(⌘/ or ?), the menu bar, `⌘N` and `⌘,` on two sidebar rows, `⌃1` on space tabs, `⌘⇧B bold…` in the note
status line.

## Appendix D: empty-state inventory

a = designed (message + primary action), b = bare text, c = blank.

| View | Renders | Class |
|---|---|---|
| New chat | greeting only (chips during onboarding) | b |
| Today | "No meetings today." + Record; Drive "No recent files."; "Could not load." | a/b |
| Agent home | "No chats yet. Start one above…", "No routines yet.", "Nothing yet." | b |
| Mail | "Connect your inbox" + Connect; "No mail here" + Clear filters / Compose | a |
| Calendar | "No events this {mode}" + New event | a |
| Lists | "Nothing open" + "Type something in the box above…" | a (no button, deliberate) |
| Files, nothing open | "Nothing open" + New menu | a |
| Uploads | `.empty-state` | a |
| Meetings | list "No meetings yet."; detail "No meeting open" + Record | b/a |
| Memory | "No memories yet." + explanation | b+ |
| Activity | 8 `empty-hint` variants with guidance | b |
| Library: Skills, Workflows | "No skills yet" + New / Import / Popular / Teach; "No workflows yet" + New | a |
| Library: Agents, Commands | "None yet. Draft one above." | b |
| Graph | "No entities yet. Chat with auto-learn on, or add one here." | b |
| Project | "Project not found" + Back; "No chats or files yet" + New chat | a |
| Health | "Every metric is hidden" + Choose metrics | a |
| Space, no windows | intro + New chat / Add widget / Got it | a |
| Space, no spaces | nothing on the plane; Overview has no empty branch | c |
| Sidebar lists | "No personal chats yet." / "No projects yet." / "No spaces yet." / "Nothing needs you." | b |
| Desk panel, widgets, inbox | "Nothing in the workspace yet." / "No uploads in this scope." / "Nothing waiting, nothing ran." | b |
