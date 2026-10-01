## Activity monitor, habit insights and PII redaction

### Where we are

**Collection.** `backend/personal_os/activity.py` (1.7k lines) runs daemon collectors: `FocusCollector` (frontmost app and window title every `sampleSeconds`, one row per stretch of attention, written on close in `_close`), `InputCollector` (listen-only CGEventTap; counts and WPM, optional redacted typed text), and `AudioCollector` (ffmpeg chunks, STT, wav deleted). Six signals in `SIGNALS`; defaults are apps plus input counts. Palantir mode (`Monitor.set_palantir`) flips everything on and snapshots/restores the old config.

**Data model.** `activity_events` (ts, kind focus/input/idle/audio/note, app, bundle, title, url, text, meta JSON, duration_ms, rolled_up, expires_at; 48h TTL), `activity_summaries` (LLM period digests, 90d), `activity_profile` (singleton, rewritten every 6h), plus insights.py tables: `activity_day_stats` (per-day max-merged aggregates: apps, hosts, hours, typing, switches, keys, clicks, scrolls, focus/idle seconds; `DayStats.merge`), and habit and suggestion tables. Output is `context/activity.md` and a trimmed block injected into chats (`Monitor.context_block`).

**The gate.** `Gate.excluded` does case-insensitive substring matching of `excludeApps` and `excludeTitlePatterns` against app, title and URL. `Gate.scrub` applies `redact.REDACTIONS` in order (private_key, email, card, ssn, token, aws_key, jwt, phone, entropy), then the `SECRET_ASSIGN` sweep. `redact.py` also exports `scrub_secrets`, the credential-only subset that meetings use.

**Insights.** `insights.mine()` is deterministic. It emits ten pattern kinds (app_routine, site_habit, thrash, deep_work, day_shape, after_hours, input_load, recurring_window, topic, switch_rate), each with a recurrence-weighted confidence (`_conf`). One LLM pass then proposes habits (one memory row each, `source: activity`) and suggestions (prompt/todo/memory/setting, sticky dismiss, snooze). A deterministic `fallback` covers a dead model.

**Weak spots, verified in code.**
1. No categorization anywhere (`grep categor` in activity.py and insights.py returns nothing). Time is "seconds per app" and "visits per host". The model is asked to guess whether Safari time was work or distraction. There is no productive/neutral/distracting axis, no "time on category X this week", and no per-day category history.
2. Redaction is regex-only with no validation.
   - `card` is `\b(?:\d[ -]*?){13,19}\b`, so any 13-19 digit run is `[card-number]`. That includes epoch millis, order IDs and file sizes, and there is no Luhn check.
   - `phone` matches any 10 digits.
   - `entropy` is a character-class heuristic with no Shannon entropy and no context.
   - There are no user allow or deny lists, and there is no per-entity score or confidence.
3. URLs are not scrubbed. `FocusCollector._close` runs `gate.scrub` on the title only; `url=cur["url"]` is stored raw, with the query string, fragments and any `?token=` or `?code=` OAuth parameters. The URL then flows into summaries via `_digest` (`urls.add(e["url"][:200])`) and so into the LLM prompt.
4. No redaction telemetry. The panel cannot say "37 emails and 2 tokens redacted today", and there is no way to test a string against the rules.
5. Exclusions are substring-only, with no regex or per-bucket rules (for example, exclude an app only when the title matches).
6. Day aggregates have no category, project or "work vs leisure" dimension, so habits like "evenings go to YouTube" cannot be detected without the LLM.

### What the best open-source systems do

**ActivityWatch** ([buckets and events](https://docs.activitywatch.net/en/latest/buckets-and-events.html), [categorization](https://docs.activitywatch.net/en/latest/features/categorization.html)).
- Events are `{timestamp, duration, data}` in per-watcher buckets. Heartbeats merge consecutive identical data inside a `pulsetime` window.
- Categories are a user-editable tree. Each category has a name path (`["Work","Programming","ActivityWatch"]`) and a rule, either a regex matched against the event's `app` and `title`, or no rule (a folder). The deepest matching sub-category wins. Totals roll up to parents.
- The query language (from my knowledge of the aw-server query transforms, not re-fetched) composes `filter_period_intersect` (clip window events to not-AFK periods), `merge_events_by_keys`, `categorize` and `sort_by_duration`. The result is that every report is "AFK-filtered, then categorized, then summed".
- Takeaway: the category tree plus deepest-match is a tiny, deterministic, model-free mechanism that makes every downstream report meaningful.

**Microsoft Presidio** ([analyzer](https://presidio.dataprivacystack.org/analyzer/)).
- The `AnalyzerEngine` runs a `RecognizerRegistry` of `PatternRecognizer`s (regex plus optional `validate_result` such as Luhn for cards and IBAN mod-97) and NER recognizers.
- Each hit carries a score. A `ContextAwareEnhancer` boosts the score when words such as "card", "ssn" or "phone" appear within a window around the match (documented defaults, from my knowledge: boost about 0.35, floor about 0.4).
- A `score_threshold`, deny lists (always flag) and allow lists (never flag) are applied. The anonymizer then applies per-entity operators (replace, mask, hash, redact).
- Takeaway: validation plus context is what separates "16 digits" from "a card number". Both are regex-friendly and need no NLP model.

**Screenpipe** ([repo](https://github.com/mediar-ai/screenpipe)).
- It is event-driven: it captures on app switch, click, typing pause or scroll rather than on a timer.
- It prefers the OS accessibility tree over OCR, falling back to Apple Vision, Windows OCR or Tesseract.
- Its filters cover window, app, password fields and a PII model. Data stays on localhost:3030.
- Its "pipes" are scheduled agents defined as markdown with YAML permissions.
- Takeaway: we already match its permission model (propose-only jobs). Accessibility-text capture is the larger leap and is out of scope, because we have no screen text signal.

**OpenRecall** ([repo](https://github.com/openrecall/openrecall)) takes periodic screenshots, runs local OCR and searches semantically. Its README documents no dedupe thresholds or privacy filters (I could not find them in the docs I fetched). It is a weaker privacy model than ours.

**Dayflow** ([repo](https://github.com/JerryZLiu/Dayflow)) chunks screen capture, has an LLM turn the chunks into timeline cards, and flags distracting sessions. The README publishes no schema or thresholds. The takeaway is that the card (title, category, distraction flag) is the user-facing unit. We can produce a category without screenshots, because the category rules above give the same axis deterministically.

**scrubadub.** Its design is detectors producing "filth" objects, then replaced by labelled placeholders such as `{{NAME}}`, with optional NER detectors (spaCy, Stanford, TextBlob). I could not retrieve its main docs, so I rely on its names page only. Our `redact.RULES` is the same shape, minus per-detector validation.

**Timing and RescueTime clones** (not fetched; from general knowledge). Category rules are ordered or specific-wins, matched against app, title and URL. Each category carries a productivity score in the range -2 to +2, which is what produces the "productivity pulse".

### Gaps

| # | gap | who does it | impact | effort |
|---|-----|-------------|--------|--------|
| 1 | Category rule tree (regex on app/title/host, deepest match wins) and per-day category seconds | ActivityWatch, Timing, RescueTime | High: unlocks reports, productivity score and category habit patterns with no LLM | M |
| 2 | Validated, context-scored redaction (Luhn, digit counts, entropy, context boost) with allow/deny lists | Presidio | High: fewer false positives and fewer misses in the keylogger path | M |
| 3 | URL sanitizing (strip query values, userinfo, fragments) before storage | Presidio-style anonymizer operators | High privacy fix: tokens in URLs are stored raw today | S (inside #2) |
| 4 | Redaction telemetry and a "test a string" box | Presidio analyzer API | Medium: user trust | S (inside #2) |
| 5 | AFK-filtered, composable query layer | ActivityWatch query language | Medium | L |
| 6 | Regex and conditional exclusions | ActivityWatch rules, Screenpipe filters | Medium | S |
| 7 | Accessibility-tree or OCR screen text | Screenpipe, OpenRecall | Large, but a new signal and heavy privacy surface | L |
| 8 | Optional NER (names, locations) | Presidio, scrubadub | Medium; needs a spaCy extra | L |

### Build next

**activity-1 (category rules).** Add `activity_categories.py` with an ActivityWatch-style rule tree: name path, optional regex over app, title and host, deepest match wins, and a productivity score on each category. It ships with a sensible default tree (Coding, Writing, Communication, Reference, Social/Entertainment, Meetings). `day_stats_from_events` gains a `cats` map of seconds per category path, merged with max like the other maps. The digest and the rollup prompt gain a "Time by category" line, `mine()` emits `category_share` and `distraction_drift` patterns, and the panel gets a category bar and an editor. The step is purely local with no model call.

**activity-2 (redaction v2).** Extend `redact.py` with validators (Luhn for cards, 10 or 11 digits with a valid area for phones, area-number checks for SSNs, Shannon entropy for the entropy rule), a context-word score boost, and user `redactAllow` and `redactDeny` lists. Add `sanitize_url` and call it from `FocusCollector._close` and the browser URL path, which fixes the raw-URL leak. Add per-entity redaction counters surfaced in `/activity/status`, and a `POST /activity/redact/test` route plus a panel box. `Gate.scrub` keeps its signature. The existing `RULES`, `REDACTIONS` ordering and `scrub_secrets` semantics must stay byte-compatible so `test_redact.py` still passes.

### Specs

#### activity-1: ActivityWatch-style category rules and per-day category stats (M)

**Why.** Our monitor knows seconds per app and visits per host but has no notion of Work vs Distraction; the LLM guesses. ActivityWatch (regex rules on app/title, name-path tree, deepest match wins, rollup to parents) and Timing/RescueTime (per-category productivity score) make this deterministic and model-free. It is the foundation that makes every report, habit and the rollup prompt more accurate, and verified absent: no 'categor' in activity.py or insights.py.

**Files.** `backend/personal_os/activity_categories.py (new)`, `backend/personal_os/insights.py`, `backend/personal_os/activity.py`, `backend/personal_os/app.py`, `backend/personal_os/tools.py`, `src/renderer/src/components/ActivityView.tsx`, `backend/tests/test_activity_categories.py (new)`, `docs/activity-monitor.md`

**Design.** 1) New module activity_categories.py. DEFAULT_CATEGORIES: list of {name: ['Work','Coding'], rule: {type:'regex', pattern:'Xcode|Cursor|Code|Terminal|iTerm|PyCharm', fields:['app','title']}, score: 2}. Include Work>Coding, Work>Writing, Work>Meetings (zoom.us|Meet|Teams|Slack huddle), Comms (Mail|Slack|Messages|Discord), Reference (stackoverflow|docs|github host), Social>Media (youtube|twitter|x.com|reddit|instagram|netflix; score -2), Uncategorized (score 0). Rules: type 'regex' (re.I, compiled once, invalid regex skipped not raised), type 'none' (folder), optional 'hosts': [..] matched against insights._host(url) with suffix match. Productivity score in -2..2, inherited from nearest ancestor if absent.
2) class CategoryEngine(rules): compile(); classify(app, title, url) -> tuple[str,...] path, picking the DEEPEST matching rule (longest name path; ties broken by list order); never raises; unmatched -> ('Uncategorized',). score_of(path). rollup(seconds_by_path) -> dict path-string 'Work/Coding' plus every ancestor prefix (so parents sum children). LRU-cache classify on (app,title_prefix_80,host).
3) Config: add to activity.DEFAULT_CONFIG key 'categories': None (None = use DEFAULT_CATEGORIES; list = user override), and add 'categories': list|None to ActivityConfigIn in app.py and make _deep_merge replace lists (it already should; verify). Also add the key to llm.DEFAULT_SETTINGS only if a top-level setting is introduced; here it lives under the 'activity' config which is already in DEFAULT_SETTINGS (SETTINGS_READ_ONLY includes activity, patched via /activity/config).
4) Schema migration: in insights.DayStats.__init__ after executescript, run PRAGMA table_info(activity_day_stats) and ALTER TABLE ADD COLUMN cats TEXT NOT NULL DEFAULT '{}' if missing (same pattern as db.Database._migrate). Add 'cats' to DayStats.MAPS so merge uses the max-merge and row_to_dict decodes JSON. Update the INSERT/UPDATE statements to include cats.
5) day_stats_from_events(events, engine=None): for focus events add secs to d['cats'][path_string] for the leaf and every ancestor prefix. Signature stays backward compatible (engine optional; default engine from DEFAULT_CATEGORIES). Insights.mine_now passes the engine built from the current config. Only category path strings and seconds are stored - no titles/URLs/text, preserving the counts-only rule.
6) New pattern kinds in insights.mine(): 'category_share' (top-level category with >=20% of focus time on >=minDays days; detail 'Work 62% of focus time, 4.1h/day') and 'distraction_drift' (days where the score-weighted share of negative-score categories exceeds 30%, with the hour band from the 'hours' map if available; confidence via _conf). Add fallback() mappings for both kinds and digest() lines. Extend the insights pass prompt's pattern list automatically through digest().
7) Monitor._digest: add a 'Time by category:' block (top 6 path strings with minutes) computed with the engine; keep output length bounded.
8) Routes in a new small router file or appended to the activity section of app.py (keep edits under ~40 lines, delegating to activity_categories): GET /activity/categories (effective rules + whether default), PUT /activity/categories (validate: names non-empty list of str, regex compiles, <=100 rules; returns 400 with the offending index), GET /activity/categories/report?days=7 returning {days:[{day, total_seconds, cats:{...}}], totals:{path:seconds}, productivity: weighted score in -2..2 or null, top_uncategorized_apps:[{app,seconds}]} read from DayStats.recent plus today's pending events.
9) Tool: add read-only 'activity_report(days)' to the activity tool group in tools.py returning the report JSON (no actions).
10) UI: in ActivityView.tsx add a 'Time by category' stacked bar on the Insights tab (top-level categories, colored from existing CSS vars) and a collapsible 'Category rules' editor listing rules with name, regex, score, add/remove/reset-to-default, and a 'Top uncategorized apps' list with a one-click 'Add rule' prefilling the app name as regex.

**Tests.** backend/tests/test_activity_categories.py, plain script style like test_insights.py (sys.path insert, assert functions, __main__ runner). Cases: deepest match wins over a parent rule; ties go to list order; 'none' folder rules never match themselves; invalid regex is skipped without raising; host suffix match (docs.github.com matches github.com); rollup sums children into every ancestor; score inheritance; day_stats_from_events with synthetic focus events produces expected cats map and no titles/URLs anywhere in the stored row; DayStats.merge max-merge idempotency and monotonicity for cats; migration adds the cats column to a DB created with the old schema (create the table by hand without cats, then instantiate DayStats); mine() emits category_share only when recurrence >= minDays and distraction_drift for a synthetic distracted evening; fallback() covers both new kinds; PUT validation rejects a bad regex; report endpoint function (call the underlying function directly, no HTTP) returns productivity within -2..2. No network, no LLM (pass a stub complete_fn that raises, to prove nothing here calls it). Also re-run test_insights.py and test_activity.py unchanged.

**Done when.** With the monitor off or on, GET /activity/categories/report returns correct category totals for seeded events; the Insights tab shows a category bar; changing a rule changes classification of future events but historical cats persist (max-merge); no model call is made by classification, stats or report; test_insights.py and test_activity.py still pass; activity_day_stats rows contain only category path strings and numbers.

#### activity-2: Redaction v2: validators, context scoring, allow/deny lists, URL sanitizing, telemetry (M)

**Why.** Presidio's recipe (regex plus validate_result such as Luhn, context-word score boost, threshold, allow/deny lists, per-entity operators) is what separates a real card number from a 16-digit order ID. Ours is blunt: the card rule matches any 13-19 digit run, phone matches any 10 digits, and URLs are stored raw (FocusCollector._close scrubs only the title), so OAuth codes and ?token= values land in activity_events and then in the rollup LLM prompt. Pure regex and stdlib, no new dependencies.

**Files.** `backend/personal_os/redact.py`, `backend/personal_os/activity.py`, `backend/personal_os/app.py`, `backend/personal_os/llm.py (only if a top-level setting is added; none expected)`, `src/renderer/src/components/ActivityView.tsx`, `backend/tests/test_redact.py`, `backend/tests/test_redact_v2.py (new)`, `docs/activity-monitor.md`

**Design.** Keep ALL existing exports byte-compatible: RULES, ALL_RULES, SECRET_RULES, REDACTIONS, SECRET_ASSIGN, scrub(), scrub_secrets(). test_redact.py must pass unchanged; meetings keep using scrub_secrets with identical output on the existing cases. Add new API alongside:
1) Validators dict VALIDATORS: name -> callable(match_text)->bool. luhn(card): strip non-digits, 13-19 digits, Luhn checksum passes. phone: digits count 10-11 (11 must start with 1), not all-same digit, not a date/epoch-looking run. ssn: area not 000/666/9xx, group not 00, serial not 0000. entropy: Shannon entropy of the matched run >= 3.5 bits/char and not a pure hex hash shorter than 40 chars when preceded by words like 'commit' or 'sha' (context). Define CONTEXT_WORDS per entity (card: card, visa, mastercard, amex, cc, payment; phone: phone, tel, call, mobile, cell; ssn: ssn, social; token: bearer, authorization, secret, key).
2) New function analyze(text, rules=ALL_RULES, *, allow=(), deny=()) -> list[Span(start,end,entity,score,replacement)]. Base score 0.5 for regexes, 0.85 for self-validating ones (private_key, aws_key, jwt, token prefixes); a failing validator drops the span unless a context word appears within 40 chars before the span, in which case it is kept at score 0.5. A passing validator adds +0.3; a context word adds +0.35 (cap 1.0). Spans with score < THRESHOLD (default 0.4) are dropped. allow: case-insensitive exact strings and compiled regexes that exempt a span; deny: strings/regexes always flagged as entity 'custom' at score 1.0. Overlaps resolved by higher score then longer span. scrub_v2(text, rules, allow, deny, counts=None) applies spans right-to-left, then the existing SECRET_ASSIGN sweep, and increments a counts dict per entity.
3) sanitize_url(url): urlsplit; drop userinfo; keep scheme+host+path; for the query keep keys but replace values with '~' when the key matches (token|code|key|secret|auth|sig|password|session|access|state|jwt|otp) case-insensitively OR the value is flagged by analyze (e.g. long high-entropy); other values truncated to 32 chars; always drop the fragment. Path segments that look like secrets (entropy rule or >= 24 chars mixed alnum) become ':redacted'. Never raises; returns '' for garbage.
4) activity.Gate: scrub() now calls redact.scrub_v2 with allow/deny from config and accumulates counts in a thread-safe Counter (self.counts, reset daily); add Gate.scrub_url(url) that returns url unchanged when cfg redact is False (Palantir) else sanitize_url. Call gate.scrub_url in FocusCollector._close (url=...) and wherever browser URLs are written. Config additions in activity.DEFAULT_CONFIG: 'redactAllow': [], 'redactDeny': [], 'redactThreshold': 0.4. Add the three fields to ActivityConfigIn in app.py. Palantir mode snapshot must also keep redactAllow/redactDeny untouched (they are not flattened by it).
5) Status: Monitor.status() includes 'redactions': {entity: count} from gate.counts (counts only, never the matched text).
6) Routes (small, delegating): POST /activity/redact/test {text} -> {redacted, spans:[{entity,score,start,end}]} using the current config; the text is processed in memory and never stored or logged.
7) UI: Privacy tab gets 'Never redact' and 'Always redact' list editors (one string or /regex/ per line), a threshold slider (0.2-0.9), a 'Redactions today' chip row from status, and a 'Test redaction' textarea showing the result live (debounced POST).
8) Docs: update the Gate bullet in docs/activity-monitor.md to describe validators, context and URL sanitizing.

**Tests.** backend/tests/test_redact_v2.py plain script, no network, no model. Cases: Luhn-valid test card 4111 1111 1111 1111 is redacted; 13-digit epoch millis 1727800000000 and a 16-digit order id failing Luhn are NOT redacted without context but ARE redacted with 'card number:' context; valid vs invalid SSN (666-12-3456 not flagged without context, 123-45-6789 flagged); phone with 10 digits flagged, '0000000000' not; entropy: a random 32-char token flagged, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' not, a 40-hex git sha after 'commit' not; allow list exempts a specific order id; deny list flags custom string and regex; overlap resolution keeps the higher-score span; threshold raise drops borderline spans; scrub_v2 counts dict increments per entity and never contains matched text; sanitize_url: userinfo stripped, ?token=abc becomes ?token=~, fragment dropped, harmless query kept and truncated, secret-looking path segment replaced, garbage input returns ''; Gate with redact False returns text and url unchanged (Palantir); Gate.scrub_url used by a stubbed FocusCollector._close writes the sanitized URL into Store (use an in-memory Database in a temp dir like test_activity.py does). Run test_redact.py, test_activity.py and the meetings redaction tests unchanged to prove backward compatibility, including that REDACTIONS order is still the dict order.

**Done when.** A URL like https://x.com/cb?code=ABC123&state=xyz#access_token=zzz is stored as https://x.com/cb?code=~&state=~ ; a Luhn-valid card is still redacted while a 16-digit non-card ID is not; existing test_redact.py, test_activity.py, test_insights.py and meetings tests pass untouched; /activity/redact/test returns spans without persisting the input; status exposes per-entity counts but no content; user allow/deny lists persist through /activity/config and survive Palantir on/off.

