# R6: Long-run autonomous knowledge work, deliverables, HITL UX

Provenance: [F] = fetched primary source this session; [S] = search-result summary (secondary); [K] = my background knowledge, not re-verified (lower confidence). The Anthropic skills xlsx/pptx/docx GitHub pages returned 503, so I read local copies of the skills (in ~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/.../skills/).

## 1. Product patterns
**Manus context engineering [F]** https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- KV-cache hit rate is "the single most important metric"; ~100:1 input:output; cached $0.30 vs $3/MTok on Sonnet. Stable prompt prefix, append-only context.
- Mask, don't remove tools: keep tool definitions fixed, constrain via logit masking / name-prefix groups in a state machine.
- File system as context: compress reversibly (keep URL/path, drop body).
- Recitation: ~50 tool calls/task average; constantly rewrite todo.md so goals sit at the end of context. (Grain has this.)
- Keep errors in context; add small variation to avoid few-shot rhythm drift.
- Manus "Wide Research" = many parallel subagents [S]; Genspark routes research into dedicated Slides/Sheets/Docs agents [S].

**Cursor cloud agents [F/S]** https://cursor.com/docs/cloud-agent/capabilities : deliverable = PR plus screenshots/videos/log references as proof; follow-ups queue until the next tool call (no interrupt); "subscriptions" wake the agent on PR comments/CI/Slack/timers in the same conversation (up to 180 days); auto-fixes CI but stops after 10 follow-ups or when a human intervenes; Slack notification on completion; subagents get isolated VMs.
**Jules [S]**: explicit session state AWAITING_PLAN_APPROVAL, `requirePlanApproval` gate. **Devin [S]**: proposes plan first, user may auto-approve. **ChatGPT agent [S]**: pauses for login/takeover (no screenshots while user drives); confirms before consequential actions.
Common pattern: plan approval, long run, artifacts as evidence (not prose claims), notify when blocked/finished, queued steering. Grain has plan approval, parked approvals, desk_ask. Missing: evidence-bearing completion, queued steering, wake-on-event.

## 2. Long-horizon reliability
- **Anthropic long-running harness [F]** https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents : initializer session builds a feature list JSON (200+ items, each passes:false), claude-progress.txt, init.sh, git commits. Each later session: check pwd, read progress + git log, smoke test, work ONE feature, commit, update. Rule: "It is unacceptable to remove or edit tests" (agent only flips the pass flag). Failure modes: premature victory declarations, undocumented progress, untested features.
- **Context engineering [F]** https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents : compaction (keep decisions/bugs, drop redundant tool output), structured notes outside context, sub-agents with clean windows returning condensed summaries, just-in-time retrieval via references. Start minimal, add instructions per observed failure.
- **False success [F]** https://arxiv.org/html/2606.09863 : false "done" is 45-48% of failures in tau2 single-control, 75.8% in AppWorld failures with explicit claims; per-model 13% to 89%; reasoning models are NOT immune (Qwen3-Max-Thinking 79%). Independent verification structure (dual-control) dropped it to 3%. Cheap TF-IDF detectors (AUROC 0.83/0.95) beat LLM judges (0.65/0.54). Implication: verify against external state, not by asking the same model. Related [S]: https://arxiv.org/pdf/2607.25152 (self-evaluation bias in long loops), https://arxiv.org/pdf/2511.05524 (evidence-bound claims).
- **Stop hooks [S]** (Claude Code): Stop hook returns block+reason to force continuation; must check `stop_hook_active` to avoid infinite loops.
- **Doom-loop detection [S]** (opencode PR #3445, OpenRouter agent SDK docs, hermes-agent issue #512): fingerprint = hash(tool name + normalized args), sliding window of 20 calls; 3 identical -> inject warning and skip execution; recurrence after warning -> escalate to user ("Allow / Break?"), one-shot allow then re-arm.

## 3. Deliverable generation (office files)
From local Anthropic skill copies [F-local]:
- **xlsx**: openpyxl for create/edit with formulas, pandas for bulk, markitdown for quick read. MUST run scripts/recalc.py (LibreOffice headless recalculates, rewrites file, returns JSON total_errors/error_summary). Rules: zero formula errors; formulas, not hardcoded results; avoid XLOOKUP/FILTER/UNIQUE/SORT (LO can't evaluate; use INDEX/MATCH); `_xlfn.` prefix for TEXTJOIN/IFS/etc.; quote sheet names with spaces; two load_workbook passes (formulas vs values); data_only=True + save destroys formulas; follow user's tab/column names literally; document assumptions in cells; financial colour conventions. "A green recalc proves formulas evaluate, not that they are right": spot-check values.
- **pptx**: pptxgenjs for new decks (set layout first, hex without #, never reuse option objects, no negative shadow offset, bullet:true not a literal bullet, native charts, theme + layouts + placeholders); unzip/edit XML for templates; thumbnail.py makes a labeled contact sheet; validate.py checks schema/relationships; LibreOffice -> PDF -> images for visual QA; markitdown to read. Bare soffice hangs in sandbox, hence a wrapper.
- **docx**: docx-js for new, unpack/edit XML for existing (tracked changes/comments), validate script, LibreOffice conversion, render to image to eyeball [K for details; I only skimmed this one].
- **pdf [F]** https://github.com/anthropics/skills/blob/main/skills/pdf/SKILL.md : pdfplumber (text+tables), pypdf (merge/split/rotate/forms), reportlab Platypus (create; use sub/super tags not Unicode), pdf2image + pytesseract for scans, qpdf/poppler CLIs.
- **GDPval [S]** https://arxiv.org/html/2510.04374v1 : scaffolding that renders and inspects output removed black-square PDFs and slide formatting errors, about +5 pts win rate. Render-and-look pays.

**Local machine probe (read-only):** present: pandoc, pdftoppm, pdftotext (poppler), tesseract, node 20. MISSING: soffice/LibreOffice, qpdf, markitdown, ocrmypdf. So today a desk cannot recalc xlsx, convert docx/pptx to PDF, or render slides to images. LibreOffice is the biggest gap (large install; make it optional).

**What each Grain skill should contain:** (a) library choice, (b) mandatory post-step (recalc / validate / render), (c) gotcha list above, (d) a QA checklist producing evidence (error count, page images), (e) save to workspace outputs/.

## 4. Reading inputs
[K; standard practice, not re-verified] Digital PDFs: pdftotext -layout / pdfplumber for tables; compute chars/page; if very low treat as scanned -> pdftoppm -r 150 then tesseract (or page images to a vision model). Layout-heavy docs (tables, charts, forms): render pages to images and look, cross-check numbers against text. Spreadsheets: per-sheet dump (shape, head, formulas in a separate pass); never print a whole sheet. Large docs: page-range reads via paged handles (Grain has them), write per-section notes to a file before moving on. Non-frontier text-only models: OCR is mandatory; flag low-confidence OCR and keep page image path in the note. Anthropic's pdf skill uses pdf2image+pytesseract for scans [F].

## 5. Multi-agent coordination
- Anthropic [F] https://www.anthropic.com/engineering/multi-agent-research-system : Opus lead + Sonnet subagents beat single Opus by 90.2% on internal research eval; agents ~4x chat tokens, multi-agent ~15x. Failures: 50+ subagents for simple queries, duplicated work from vague task descriptions, endless searching, SEO-farm sources, continuing after enough results. Fixes: scaling rules in prompt, explicit objective/output format/boundaries per subagent, start broad then narrow. Poor fit: shared-context tasks, heavy interdependence, most coding, low-value tasks.
- Cognition [S] "Don't build multi-agents": parallel subagents lacking shared context make conflicting decisions; keep a single writer, use subagents for read-only investigation.
- MAST [S] https://arxiv.org/pdf/2503.13657 : 1642 traces, 14 failure modes; system design 41.8% (step repetition 15.7%, unaware of termination 12.4%, disobey spec 11.8%), inter-agent misalignment 36.9%, verification 21.3%; single-pass verification insufficient.
- Patterns: orchestrator-workers, evaluator-optimizer (generator + critic, bounded loop) [S] https://github.com/anthropics/claude-cookbooks/blob/main/patterns/agents/README.md . Shared task boards / agent-team messaging [K]: pays off for wide, independent, read-heavy work; hurts for single-deliverable assembly.
- Rule: parallelize reading/research/independent sections; ONE agent owns plan and final assembly; reviewer is read-only and fresh-context.

## 6. Evaluation
- GDPval [S]: expert-graded real deliverables; Opus 4.1 47.6% wins-or-ties (Sept 2025), GPT-5 38.8%, o3 34.1%; GPT-5.2 Thinking later reported 70.9% (secondary). Losses dominated by instruction-following (Claude/Gemini/Grok) and formatting (GPT-5); "acceptable but subpar"; automated grader only 66% agreement with humans.
- TheAgentCompany [S] https://papers.nips.cc/paper_files/paper/2025/file/0d744742f6fac4d1134c019b7cef3c8a-Paper-Datasets_and_Benchmarks_Track.pdf : best (Gemini 2.5 Pro) 30.3% full / 39.3% partial, ~27 steps, $4.20/task. Failures: not following up with colleagues (asking), declaring done without verification, office-suite UI, tedious multi-document cross-referencing.
- METR [S] https://metr.org/blog/2026-1-29-time-horizon-1-1/ : TH1.1; historical doubling ~7 months, recent estimates ~188 days; Claude 3.7 ~60 min to Opus 4.6 ~718 min p50 (software tasks, 50% success; 80% horizon much shorter).
- OSWorld, GAIA, SWE-Lancer: not researched this session; no claims made.
- Implications for non-frontier models: expect reliable horizons of minutes-to-low-hours. (1) false success is higher and reasoning doesn't fix it, so external gates; (2) instruction-following misses dominate, so extract an explicit requirements checklist at plan time and verify at end; (3) verify format by rendering; (4) small steps, state on disk; (5) ask clarifying questions early (not asking is a top failure).

## Mechanisms worth copying (concrete)
1. **Deliverables manifest** `workspace/.desk/deliverables.json`: [{path, kind, requirement_ids, status: draft|checked, evidence:{recalc_errors:0, pages_rendered:[...], validator:"ok"}}]. A desk cannot be marked done until every required deliverable exists in outputs/ with evidence.
2. **Requirements checklist** extracted at plan approval into `requirements.md` (`[ ] R1 ...`: filenames, tab names, length, tone, audience, format). Only the status flag is editable; prompt: "It is unacceptable to remove or edit requirements." Reviewer ticks each with quoted evidence.
3. **PROGRESS.md** appended each chain link (done / next / blockers / decisions / file index); first action of each reply is to read it. Todo re-injection exists; this adds decisions + file index.
4. **Completion gate (Stop-hook analogue)**: on a final answer, the harness checks (a) open todos, (b) required deliverables have evidence, (c) files open cleanly (zip test, openpyxl load, pdftotext >0 chars). On failure inject "Not done: <list>. Continue." Cap 3 refusals, then park to the user with the reason (stop_hook_active equivalent).
5. **Independent reviewer pass** (existing reviewer subagent) on requirements.md + deliverables + page images, read-only, pass/fail per requirement with quotes; cap 2 fix cycles. Pair with deterministic checks since LLM-only judges are weak.
6. **Doom-loop guard**: fingerprint(tool, normalized args), window 20; 3x -> warning and skip; again -> park a "stuck" approval. Plus stagnation: N=8 rounds with no new file bytes / todo change / new sources.
7. **render_preview(path)** tool: docx/pptx/xlsx -> pdf (soffice) -> pdftoppm PNG ~80 dpi -> image handles (or OCR text + layout stats for text-only models). Mandatory in office skills before marking "checked".
8. **Queued steering**: a user message mid-run is stored as a `steer` row and injected before the next tool call (Cursor behaviour), not abort-and-restart.
9. **Ask-early rule**: the planner must issue desk_ask for blocker-grade ambiguity BEFORE plan approval, stating defaults so the user can just approve.
10. **Cache-stable prompts**: identical system prompt + tool list across a desk's life; plan/todo/progress at the end; no timestamps in the prefix.
11. **Evidence in the review tab**: per-requirement thumbnails/logs (like screenshots attached to a PR) instead of prose claims.

## Not worth copying / risks
- Agent teams / messaging bus for single-deliverable tasks: ~15x tokens, MAST misalignment 37%. Parallelize only read-only research and independent sections.
- Logit-masking tools (needs engine access; hosted LiteLLM models rarely expose it). Use fixed tool list + permission gate.
- LLM-as-sole-judge of "done"; raising maxToolRounds or heartbeats (already anti-goals).
- Uncapped evaluator-optimizer loops; stop-gates without a refusal counter.
- Bundling full LibreOffice by default; make it an optional install with fallbacks (python-docx/docx-js without render; compute xlsx values in Python).
- Copying Anthropic's skill files verbatim (proprietary licence); write our own from the gotcha lists.
- Cloud-VM isolation (Cursor): out of scope for local-first; shadow-git + sandbox shell suffice.

## Ranked recommendations for Grain (on top of what exists)
1. **Completion gate + deliverables manifest** (items 1,4): refuse final "done" while todos are open or required outputs lack evidence; 3-refusal cap then park to user. False success is the dominant long-run failure and external gating works (45% to 3%). Effort M.
2. **Requirements checklist at plan time + reviewer pass against it** (items 2,5): reuse the reviewer subagent, fresh context, read-only, quoted evidence, max 2 fix cycles, shown in review tab. Instruction-following misses top GDPval/TAC losses. Effort M.
3. **Office toolchain + render_preview**: optional LibreOffice install check/onboarding step, our own recalc/validate/render scripts. No soffice on this machine today; render-and-look gave measurable gains. Effort L packaging, M if soffice assumed.
4. **Four office skills (docx/xlsx/pptx/pdf)** as bundled approve-once skills with section-3 gotchas and mandatory QA steps. Effort S-M after #3.
5. **Doom-loop + stagnation detector** that parks a "stuck" approval with the last 3 actions. Effort S.
6. **PROGRESS.md + session-start protocol** per desk, complementing compaction. Effort S.
7. **read_document tool**: detect text vs scanned PDF (chars/page), OCR via tesseract (installed), pdfplumber tables, two-pass spreadsheet dump, output as paged handle + page-image paths. Effort M.
8. **Queued steering + wake-on-event** (note delivered at next tool boundary; desks subscribe to time/file/email triggers via existing scheduled jobs). Effort M.
9. **Ask-early at planning** (assumptions list + desk_ask before approval). Effort S.
10. **Fan-out research discipline**: scaling rules in prompt (1 / 2-4 / 10+), explicit objective/output/boundaries per subagent, notes written to workspace/notes/ with paths returned, single assembler. Effort S-M.
Plus: a small eval set (10 fixed knowledge-work tasks: xlsx model, 8-slide deck, memo from 3 PDFs) scored on requirements ticked + files valid, run per model to calibrate step size for non-frontier models. Effort M.
