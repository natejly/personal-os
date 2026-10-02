# Docs as a note-taking surface, and voice in it

Research for two things: (A) which note-app features are worth adding to the Docs editor without leaving the textarea-over-mirror design, and (B) how to get live dictation and long-form session recording into a note on macOS using what the backend already has.

Verification key: **[code]** read in this repo. **[src]** from the linked page. **[prior]** from background knowledge, not re-checked here. **[unverified]** could not confirm; test before relying on it.

## What exists today (read from the repo)

- `MarkdownEditor.tsx` [code]: `<textarea class="md-input">` over `<pre class="md-mirror">` filled by a line-oriented `highlight()` (one output line per input line, so layers cannot drift). Status bar already shows Ln/Col, lines, words. Key handlers: bold, italic, code, maths, link, Tab indent, Enter list continuation. Edits go through `apply()`, which does `el.setRangeText(...)` then `onChange`.
- `DocsView.tsx` [code]: search box, folder tree, history panel, `docMode` (edit / split / preview), linked scroll, `MarkdownPreview` for the preview, `ResizeHandle` panes.
- Backend audio [code]: `native_audio.Capture` is a long-lived 16 kHz mono s16 PCM source (AVAudioEngine tap for mic, Core Audio process tap for system output) with a `_PcmSink` ring buffer (30 s cap) and `read_seconds()`. `stt.transcribe(path)` takes one wav and returns `{text, detail, backend, error, ms}`; backends are `speech` (`SFSpeechURLRecognitionRequest`, `requiresOnDeviceRecognition=True`, `shouldReportPartialResults=False`, pumping an NSRunLoop until `isFinal`), `local` (`whisper-cli`), `proxy` (`/v1/audio/transcriptions`). `meetings.py` records 20 s segments (`segmentSeconds: 20`), gates silent ones with `meeting_vad.analyze` (stdlib energy VAD), filters hallucinations, and keeps failed segments for `retranscribe`.
- `build/entitlements.mac.plist` [code] already has `com.apple.security.device.audio-input`, and `electron-builder.config.cjs` has `NSMicrophoneUsageDescription` and `NSSpeechRecognitionUsageDescription`. So both TCC prompts are already declared for the packaged app.
- Observation worth testing [unverified]: the code comment on `apply()` says `setRangeText` keeps edits on the native undo stack. A web search summary says the same, but my recollection [prior] is that in Chromium `setRangeText` and assigning `.value` do not create undo entries (only `document.execCommand('insertText')` does). If that is right, every ⌘B, Tab, list-continue and any future slash/dictation insert already wipes ⌘Z history. A 5-minute check in the running app settles it, and Part B item 5 depends on it.

---

# Part A. Note-taking features

## What the reference apps are known for [prior, no single source]

| App | What people reach for |
| --- | --- |
| Obsidian | `[[wikilinks]]` + backlinks, daily notes, templates, tags, outline pane, quick switcher (⌘O), slash commands via core plugin |
| Bear | `#tags` / nested tags, sidebar by tag, pin, note linking, export |
| Apple Notes | checklists, quick note from any app, pinned notes, scan/paste images, smart link previews |
| Notion | `/` menu, templates, favourites, checkbox blocks, table of contents block |
| Craft | `/` and `@` menus, daily notes, backlinks, PDF export |
| Logseq | daily journal as the default page, `[[refs]]`, `#tags`, backlinks panel, TODO states |
| iA Writer | focus mode (dim all but the current sentence/paragraph), typewriter scroll, reading time, syntax highlight of parts of speech |

The common core that matters for a personal OS: fast capture, links between notes, tasks, structure navigation, and getting content in (paste, voice).

## Candidates, implementation here, cost

Costs: S = under a day, M = 1-3 days, L = a week or more.

### 1. Slash-command menu at the caret (cost M, P0)
- Trigger: in `onChange`/`onKeyDown`, if the character before the caret is `/` and it is at line start or after whitespace, open a menu anchored to the caret. Filter as the user types after the slash; Esc / space-with-no-match / caret leaving the token closes it. Up/Down/Enter/Tab are intercepted only while open (same pattern the list-continuation Enter handler already uses).
- Caret position: the **mirror-div technique**. Create an off-screen div styled identically to the textarea (copy `font`, `padding`, `border`, `white-space: pre-wrap`, `word-wrap`, `width`, `letter-spacing`, `tab-size`), put `value.slice(0, selectionStart)` in it, append a `<span>`, and read `span.offsetTop/offsetLeft`. Subtract `textarea.scrollTop/scrollLeft`, add the textarea's rect. This is what the `component/textarea-caret-position` library does and returns `{top, left, height}` ([src](https://github.com/component/textarea-caret-position)). **Grain shortcut:** the app already has a pixel-identical `md-mirror` behind the textarea driven by the same `--ed-*` metrics, so skip the library: render the text before the caret into the existing mirror with a marker span (or measure with a `Range` on the mirror's text node) and read its `getBoundingClientRect()`. Because lines map 1:1, you can also compute `top` from line index times line height when wrap is off.
- Items: heading 1-3, bullet, numbered, task, quote, code fence, table, math block, divider, date (today), "dictate here", "insert template", "link to doc".
- Insertion: replace `/query` with the snippet via the shared insert helper (see B5 for the undo-safe version) and place the caret at a `$0` marker.

### 2. Formatting toolbar (cost S, P1)
A thin row of buttons above the editor calling the existing `wrapSelection`/`shiftLines`/prefix helpers. Mostly wiring; value is discoverability and touch/trackpad users. Add line-prefix toggles (heading, bullet, task, quote) that the keyboard handlers do not cover.

### 3. Clickable task checkboxes in the preview (cost S-M, P0)
`MarkdownPreview` renders GFM task items as `<input type="checkbox" disabled>`. Needs source line numbers: pass a rehype/remark plugin (or react-markdown's `node.position.start.line` on the `li` component) so each task checkbox knows its 1-based source line. On click, the preview calls `onToggleTask(line)`, DocsView rewrites that one line (`- [ ]` <-> `- [x]`, regex `^(\s*[-*+]\s+\[)([ xX])(\])`) and saves through the normal path so it lands in revision history. Guard: if the line at that number no longer matches the task pattern (stale preview), do nothing. Pairs naturally with the todos module: a `- [ ]` line can later offer "add to Todos".

### 4. `[[wikilinks]]` with autocomplete and backlinks (cost M-L, P1)
- Parse: `/\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]/g`. Highlight in the mirror's `span()` (add one alternation, class `tk-wikilink`; stays line-local).
- Autocomplete: same popup machinery as slash, triggered by `[[`; candidates from the doc list the sidebar already loads (title match, recents first); insert `[[Title]]`.
- Resolution: by title (case-insensitive), falling back to "create doc" on click. Titles can change, so resolve at render time, not by storing ids in text. (Store-by-id with `[[id|Title]]` is more robust but ugly to read in a plain textarea; title-based is what Obsidian does.)
- Preview: a remark plugin turns the token into a link node with a `grain-doc:` URL; DocsView intercepts clicks and opens the doc.
- Backlinks: backend, on every doc save, extract link targets into a `doc_links(src_id, target_title)` table (small, rebuilt per save), expose `GET /docs/{id}/backlinks`; a "Linked from" footer under the preview. Also lets `doc_read` tell the assistant what links where.
- Cost driver is the backend table + rename handling, not the editor.

### 5. Daily note (cost S, P0)
A "Today" command (palette, sidebar button, and ⌃⌘D-style shortcut) that finds-or-creates a doc titled `YYYY-MM-DD` in a `Daily` folder (the folder tree and `doc_create` exist) from a template. Pair with the Mac-side quick capture (item 13). Cheap and high return because it gives dictation a default destination.

### 6. Templates (cost S, P1)
Docs whose folder is `Templates` (or a `template: true` flag) show in a "New from template" menu and the slash menu; variables `{{date}}`, `{{time}}`, `{{title}}`, `{{cursor}}`. Meetings already has templates (`tpl` in `meetings.py`); reuse the naming.

### 7. Pinned / favourite docs (cost S, P1)
`pinned INTEGER` on `docs`, sorted first in the tree and in a "Pinned" group above the folders. Drag-reorder is a later nicety.

### 8. Tags (`#tag`) (cost M, P2)
Parse `(^|\s)#([A-Za-z][\w/-]*)` outside code and headings; highlight in the mirror; backend extracts tags on save into `doc_tags`; sidebar filter chips; search box accepts `#tag`. Collides with `# heading` only if you forget the required non-space after `#`. Defer until wikilinks exist because both share the extraction hook.

### 9. Outline / table-of-contents panel (cost S, P0)
Derive from the existing `value.split('\n')` pass: lines matching `^#{1,6}\s`, skipping fenced code and `$$` blocks (the `highlight()` function already tracks both states; export the heading list from the same loop). Render as a collapsible list in the doc's right edge (a `ResizeHandle` pane already exists); clicking a heading sets `textarea.selectionStart` and scrolls to `line * lineHeight`, and scrolls the preview via the existing linked-scroll fraction.

### 10. Word count, reading time (cost S, P0)
Words already shown. Add reading time (`words / 230`), character count, and selection count ("12 of 840 words") using `selectionStart/End` that `trackCaret` already reads.

### 11. Paste handling: images and smart URL paste (cost M, P1)
- URL paste: in `onPaste`, if clipboard text matches `^https?://\S+$` and there is a non-empty selection, wrap as `[selection](url)`; with no selection insert `<url>` or, better, insert `[url](url)` and asynchronously fetch the page title through the backend (there is a `fetch_url` tool) to replace the label. Cmd+Shift+V pastes plain.
- Image paste: `clipboardData.files[0]` is an image -> POST to a new `/docs/{id}/assets` (backend stores under the data dir, returns a stable relative URL), insert `![](url)` at the caret. Preview needs the asset route to be reachable with the app's auth token (the main-process fetch note in memory says auth headers matter). Drag-drop uses the same path.

### 12. Export (cost S-M, P1)
Markdown: download `title.md` (a `Blob` + anchor). Print/PDF: Electron `webContents.printToPDF` on a hidden window rendering the preview with a print stylesheet; or `window.print()` on the preview pane with `@media print` hiding chrome. KaTeX CSS already loads for the preview, so maths prints. HTML export is the same render serialized.

### 13. Quick capture global shortcut (cost M, P1)
Electron `globalShortcut.register('CommandOrControl+Shift+Space', ...)` in main opens a small always-on-top frameless window (or the main window to the daily note with the caret at the bottom) with a textarea and a mic button; Enter appends a timestamped bullet to today's daily note via the backend. Main process already has an Electron bridge pattern (loopback + secret) from the Mac-reach work in memory. This is where hold-to-talk voice capture is most useful.

### 14. Focus / typewriter mode (cost S, P2)
Typewriter: after each input, set `textarea.scrollTop` so the caret line sits at about 45% of `clientHeight` (the mirror follows through `syncScroll`). Focus (dim other paragraphs): mirror-only, wrap lines outside the current paragraph in a `dim` class in `highlight(src, activeLine)`; costs a re-highlight on caret move, so memoize per paragraph. Hide gutter and status bar in this mode.

## Recommended order for Part A

**P0 (cheap, compounding):** outline panel; word count / reading time / selection count; clickable task checkboxes; slash menu (builds the popup + insert helper that everything else reuses); daily note.

**P1:** `[[wikilinks]]` + backlinks (reuses slash popup); pinned docs; templates; smart URL paste and image paste; markdown + PDF export; formatting toolbar; global quick capture.

**P2:** tags; focus/typewriter mode.

### Recommendation for Grain (Part A)
1. Build one reusable `useCaretPopup(textarea, mirror)` + `insertAtCaret(el, text)` pair first. Slash menu, wikilink autocomplete, `@date`, and dictation all sit on them.
2. Keep every new syntax highlight inside `span()`/`inline()` so the one-output-line-per-input-line invariant holds. Wikilinks and tags are single-line tokens; fine.
3. Do task toggling by source line number, saved as a normal user revision; do not keep a second copy of state in the preview.
4. Put link/tag extraction on the backend save path once, and feed both backlinks and tag filters from it.
5. Run the undo check described above before adding more programmatic edits.

---

# Part B. Dictation and live transcription on macOS

## B1. Apple Speech framework

### SFSpeechRecognizer streaming from Python
- `SFSpeechAudioBufferRecognitionRequest` accepts a continuous feed of audio buffers (`appendAudioPCMBuffer_`, `endAudio`) and, with `shouldReportPartialResults=True`, calls the result handler with growing hypotheses; `result.isFinal()` marks the last. `requiresOnDeviceRecognition=True` keeps audio local; check `recognizer.supportsOnDeviceRecognition()` first. The existing `_speech()` already uses the URL variant with these calls via `pyobjc-framework-Speech`, so the bridge, the authorization dance and the NSRunLoop pump are proven in this codebase [code]. See Apple's [SFSpeechRecognizer docs](https://developer.apple.com/documentation/speech/sfspeechrecognizer) for the API and for the Info.plist usage strings [src].
- Feeding bytes: needs an `AVAudioPCMBuffer` built from the s16 PCM that `Capture.sink` yields. In pyobjc that means `AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(AVAudioPCMFormatFloat32, 16000, 1, False)`, `AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_`, then writing float samples into `floatChannelData()`; the repo already has the opposite direction (`_buffer_to_s16_mono`, `_ptr_bytes`, `_float_channels`) in `native_audio.py`, so the pointer handling is a known quantity [code]. Simpler alternative that avoids building buffers: let SFSpeech own the mic by calling `request.appendAudioPCMBuffer_` directly from the same AVAudioEngine tap callback `native_audio` already installs (the tap hands you an `AVAudioPCMBuffer` already). Splitting the buffer to both the sink and the request is a small change to `_start_engine`'s tap block. [unverified: not prototyped here]
- Limits:
  - A frequently quoted limit is **about one minute of audio per request** and the task is stopped after that ([search summaries of Apple forum threads and tutorials](https://developer.apple.com/forums/thread/82839); [Andy Ibanez](https://www.andyibanez.com/posts/speech-recognition-sfspeechrecognizer/)). Those sources frame it for server-based recognition; reports that on-device requests run longer exist but I could not confirm a documented figure. Treat it as: **plan to roll the request every 45-55 s** (end the request, start a new one at a quiet moment) either way. [unverified for on-device]
  - Server recognition (not on-device) also has per-device daily request limits [prior]. On-device avoids it.
  - Accuracy of the classic on-device model is below current Whisper-class models and punctuation is limited [prior].
  - Needs a pumped run loop on the thread that created the task; `_speech()` does that with `NSRunLoop.runMode_beforeDate_`, and `native_audio` has `_pump_runloop` [code].
- Authorization from a non-bundled Python process: TCC attributes access to the "responsible process". When the Python backend is spawned by the signed Electron app, prompts and grants attach to the app, and the app declares `NSSpeechRecognitionUsageDescription` and `NSMicrophoneUsageDescription` plus the `audio-input` entitlement [code]. A dev-time `python` run from a terminal is attributed to the terminal app instead, which is why the existing `stt.capabilities()` probes the status and shows a Grant button [code]. A bare Python binary that lacks `NSSpeechRecognitionUsageDescription` in its own Info.plist will crash on `requestAuthorization` when it is the responsible process [prior; the repo's design of only requesting from the app-spawned backend avoids it].

### SpeechAnalyzer / SpeechTranscriber (macOS 26)
- From [WWDC25 session 277](https://developer.apple.com/videos/play/wwdc2025/277/) [src]: `SpeechAnalyzer` runs an analysis session; `SpeechTranscriber(locale:, transcriptionOptions:, reportingOptions: [.volatileResults], attributeOptions: [.audioTimeRange])` is the module producing text. Audio goes in through an `AsyncStream<AnalyzerInput>` (`analyzer.start(inputSequence:)`), converted to `SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith:)`. Results arrive on `transcriber.results` as an async sequence; **volatile** results are fast approximations that are replaced, **final** results are stable. Models are fully on-device and managed by `AssetInventory` (`assetInstallationRequest(supporting:)` then `downloadAndInstall()`), stored in system storage not the app. It is described as built for long-form and distant audio (lectures, meetings) and powers Notes and Voice Memos. `DictationTranscriber` is the fallback for unsupported locales/devices.
- File mode speed: one project measured about 60x realtime on Apple silicon through a Swift helper ([mac-transcriber](https://github.com/yrocaz/mac-transcriber)) [src].
- **Availability from pyobjc: poor, as far as I can tell.** The API is async/await Swift. [pyobjc's Speech API notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Speech.html) mention nothing about it [src], and Swift-only async APIs without `@objc` are not reachable from the Objective-C runtime that pyobjc uses [prior; I did not confirm that `SpeechAnalyzer` lacks an ObjC projection]. The practical routes people use are a **small Swift helper binary that streams NDJSON on stdout** (mac-transcriber does exactly this, file-only) or a Swift/C bridge wheel: [mac-speech-analyzer on PyPI](https://pypi.org/project/mac-speech-analyzer/) is that, macOS 26 ARM64 only, **file transcription only; live mic is on its roadmap, not shipped** [src].
- Implication: SpeechAnalyzer gives the best on-device quality and a first-class volatile/final model, but costs a compiled Swift helper (signed, bundled in the DMG, macOS 26+ only). Worth doing as a phase 2 behind the same event schema as the Python SFSpeech path.

## B2. Whisper-family and other streaming engines

| Engine | How streaming works | Latency / accuracy | Mac install burden | Drive from Python without torch? |
| --- | --- | --- | --- | --- |
| whisper.cpp `whisper-stream` ([README](https://github.com/ggml-org/whisper.cpp/tree/master/examples/stream)) | Re-transcribes a rolling window every `--step` ms; VAD mode (`--step 0`) triggers on speech [src] | Choppy: rewrites the line in place, no stability policy; practical delay 1-3 s on base/small with Metal [prior] | Needs SDL2 and a `-DWHISPER_SDL2=ON` build; `brew install whisper-cpp` does not ship it [src]. Repo already uses `whisper-cli` | Spawn it as a subprocess and parse stdout, or (better) reuse `whisper-cli` on short windows yourself. No torch |
| whisper_streaming / LocalAgreement-n ([ufal](https://github.com/ufal/whisper_streaming)) | Re-run Whisper on a growing buffer and **commit only the prefix that n consecutive runs agree on**; trim buffer at sentence ends [src] | About 3.3 s latency on long-form in the paper [src]; very stable text | Library pulls librosa/soundfile and a backend (faster-whisper, whisper-timestamped, MLX) [src]; torch only for optional VAD | The *policy* is ~100 lines; you can implement it over `whisper-cli`/proxy calls without the package |
| faster-whisper | CTranslate2; not streaming itself, used as the backend above | Fast on CUDA/CPU; on Apple silicon it runs CPU only (no Metal) [prior], so slower than whisper.cpp or MLX | `pip install faster-whisper`, no torch needed (CT2 + onnxruntime for VAD) [prior] | Yes, but poor fit on Mac |
| WhisperKit / argmax ([repo](https://github.com/argmaxinc/WhisperKit)) | Swift package, CoreML/ANE; CLI plus an **OpenAI-compatible local server** with streaming output (`swift run argmax-cli serve`) [src]. Real-time-with-speakers is in the paid Pro SDK [src] | Fast on ANE; accuracy is Whisper's [prior] | Needs Xcode 16 and macOS 14+ to build [src]; models downloaded on first use. Heavy for a user-install step | Yes: the existing `proxy` backend already speaks `/v1/audio/transcriptions`, so pointing `sttProxy` at the local server is zero new Python code |
| NVIDIA Parakeet via [parakeet-mlx](https://github.com/senstella/parakeet-mlx) | `transcribe_stream()` with context windows, exposes finalized and draft tokens [src] | Very accurate for English, fast on M-series [prior] | `pip install parakeet-mlx`, needs ffmpeg for CLI, Apple silicon only [src]; pulls MLX, not torch | Yes, no torch (MLX). Heavier dependency than ONNX |
| sherpa-onnx streaming transducers ([docs](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/index.html), [mic example](https://k2-fsa.github.io/sherpa/onnx/python/real-time-speech-recongition-from-a-microphone.html)) | **True streaming** zipformer/LSTM transducers with built-in endpoint detection; per-chunk partial result [src] | Tens-to-hundreds of ms latency [prior]; English streaming zipformers (20M, 2023-06-26, etc., fp32 and int8) are mid-pack accuracy, noticeably below Whisper on hard audio [prior] | `pip install sherpa-onnx`, ONNX Runtime only, models are a download [src]. Newer Parakeet/Whisper non-streaming ONNX exports also run in sherpa-onnx [prior] | Yes, no torch. Best "drive it from Python" streaming option |
| Moonshine v2 ([moonshine-voice](https://github.com/moonshine-ai/moonshine), [paper](https://arxiv.org/html/2602.12241v1)) | Streaming encoder with cached state, incremental audio [src] | Reported response latency on Apple M3: Tiny 50 ms, Small 148 ms, Medium 258 ms [src (search summary of the paper)]; claims accuracy at or above Whisper Large v3 for the larger model [src, vendor claim, unverified] | `pip install moonshine-voice`, MIT, ONNX not torch, macOS supported [src] | Yes, no torch. Strongest "fast, small, Python" candidate; check English-only coverage and the non-commercial licence on legacy non-English models [src] |

Summary: for a Python backend with no torch, **Apple Speech (existing), sherpa-onnx, Moonshine** are the live-capable choices. **whisper.cpp / proxy** are best for final quality on closed segments. **WhisperKit server** is the zero-code way to get fast Whisper if the user installs it.

## B3. Inside Electron

- **Web Speech API (`webkitSpeechRecognition`) does not work in Electron.** It posts audio to Google's speech service with a Chrome-only key; Electron's Chromium has none, so it fails with a `network` error ([electron#7749](https://github.com/electron/electron/issues/7749), [chromium-dev thread](https://groups.google.com/a/chromium.org/g/chromium-dev/c/RBZXqc0Zvmc)) [src]. `GOOGLE_API_KEY` support was added for geolocation, not speech [src]. Do not build on it. It would also send audio to Google, against the app's local-first stance.
- **`getUserMedia` + AudioWorklet in the renderer** works in Electron (it is Chromium) [prior]. Capture mono, run an `AudioWorkletProcessor` that downsamples to 16 kHz and posts Int16 frames of 20-100 ms to the main thread, then ship over a WebSocket (`/ws/dictate`) to FastAPI as binary messages. Pros: the browser owns echo cancellation / noise suppression / AGC (`getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}})`), the mic indicator and permission belong to the visible window, and it works in any OS later. Cons: a second capture path next to `native_audio`; ~5-20 ms extra buffering; the renderer must stay alive (fine for a foreground editor). `MediaRecorder` produces opus/webm chunks that need decoding on the backend; for streaming STT raw PCM from an AudioWorklet is better.
- **macOS permission handling.** In main: `systemPreferences.getMediaAccessStatus('microphone')` returns `not-determined | granted | denied | restricted | unknown`; `systemPreferences.askForMediaAccess('microphone')` resolves a boolean; if the user changes it later in System Settings the app must restart for it to take effect ([Electron docs](https://www.electronjs.org/docs/latest/api/system-preferences)) [src]. `NSMicrophoneUsageDescription` supplies the prompt text [src]. With the hardened runtime, **`com.apple.security.device.audio-input` must be in the entitlements of the app and its helper processes, otherwise TCC silently denies without prompting** ([example reports](https://github.com/NousResearch/hermes-agent/issues/37718), [CowAgent #3180](https://github.com/zhayujie/CowAgent/issues/3180)) [src]. Grain already sets it and uses one plist for both `entitlements` and `entitlementsInherit` [code]. For a renderer `getUserMedia`, also handle the session's permission handlers: the app has `setPermissionRequestHandler` returning false in `pagefetch.ts` for that webview session [code]; the main window's session must allow `media` for the app's own origin. [unverified: not checked for the main session]
- **Which capture path?** Keep `native_audio.Capture` for meetings (system output tap, multi-channel, survives the window closing). For caret dictation, either path works; the renderer path is better if STT runs in the backend over a socket anyway (no second AVAudioEngine on the Python side competing with a meeting recording for the mic), and the Python path is better if Apple Speech runs in-process, since buffers are already there. See B4.

## B4. Pragmatic low-latency design given what exists

### What the existing pipeline can and cannot do
Segments are fixed 20 s windows, so text arrives 20 s plus STT time late [code]. Shortening to 5 s makes each cut land mid-word, which hurts every backend and multiplies requests and rows (the code comments already worry about reindexing four FTS columns every 20 s [code]). So don't make the durable path faster; **add a preview path beside it.**

### Options
1. **Overlapping short windows, replaced by the final segment.** Every ~2-3 s, take the last 8-10 s of audio from the capture ring buffer, transcribe to a temp wav with whichever backend is fastest (Speech URL request or `whisper-cli` base.en), apply LocalAgreement-2 against the previous run to split `committed` from `tentative`, and show it greyed. When the 20 s segment lands, drop the preview for that time span. No new dependency; cost is repeated work (about 4x the audio transcribed) and per-call process or run loop overhead (`whisper-cli` model load per call is the weak point; `speech` is cheaper). Expect 2-4 s visible lag. Works with the existing `stt.transcribe`.
2. **Apple Speech streaming partials** (B1). Single long-lived request fed from the tap, partials every few hundred ms, roll over around 50 s. Lowest latency with zero new installs, on-device, works for both modes. Weaker accuracy and punctuation than Whisper; so keep it as preview only and let the durable path correct it.
3. **VAD-aligned segment cuts.** Make the durable segmenter cut at silence between about 8 and 25 s using `meeting_vad`'s frame analysis (it already computes speech spans and `MERGE_GAP_S`), instead of a hard 20 s. Better accuracy, lower typical lag (many cuts at 8-12 s in conversation), no new engine. Independent of 1 and 2 and worth doing anyway.
4. **sherpa-onnx or Moonshine streaming** for preview. Best latency and no Apple permission dependency, but a new model download and dependency to ship; good second step if Apple Speech partials prove too poor.
5. **SpeechAnalyzer via a Swift helper** on macOS 26+: best quality volatile/final stream. Phase 2.

### Recommendation
- **Source of truth stays the durable segments** (VAD-aligned, whisper/Speech final), including `retranscribe`, hallucination filter and VAD gate, for the long-form "record this session" mode.
- **Add a preview channel** that emits `{kind: 'volatile'|'final', text, t0, t1}` events over the existing app-wide `/events` topic (or a dedicated SSE/WebSocket per dictation session). Implement it first with option 2 (Apple Speech buffer request with partials, rolled every ~50 s at a quiet point), with option 1 as the fallback for Macs/locales where `supportsOnDeviceRecognition` is false and a `whisper-cli` is present.
- **Simplest version that feels live:** a "Record" button in the editor header. Backend starts one `Capture('mic')`, one `SFSpeechAudioBufferRecognitionRequest` (on-device, partials on), and the normal 20 s segment writer to a meeting-like row. The editor shows partials as a greyed block at the end of the note (or at the caret), committing to real text when the final arrives. If Speech is unavailable, show the greyed text from option 1 instead. This is one new backend module (about 200 lines: tap fan-out, request lifecycle, event emit) and one small editor overlay.
- Do **not** raise `segmentSeconds` below 10; do cut at silence.

## B5. Voice typing at the caret

### How dictation tools behave [prior]
- **Hold-to-talk vs toggle.** macOS Dictation and most modern tools offer both: hold a key (Fn-style) for push-to-talk, tap to latch for hands-free; push-to-talk gets the cleanest turn boundaries and avoids VAD problems. Offer ⌃⌘D-style toggle and a hold chord in the editor, with an on-screen indicator (pulsing mic, waveform).
- **Provisional vs committed text.** Show the unstable tail greyed/underlined at the caret and replace it as it firms up (the volatile/final split in SpeechAnalyzer, partial/final in SFSpeech, or committed/tentative from LocalAgreement). Only the committed prefix is written into the document and the undo stack.
- **Cleanup.** Raw ASR text often lacks capitalisation/punctuation or has fillers. Do a cheap rule pass in the renderer (capitalise sentence starts, strip "um/uh", spoken-punctuation words) and an optional LLM pass at the end of the utterance on the committed text only, with a strict prompt "fix punctuation, capitalisation, remove fillers; do not rephrase or add content", short timeout, and fall back to the raw text. The app already has the LLM plumbing and an existing voice-profile feature (`style.py` in memory) for tone; don't apply the style profile here.
- **Command words.** A small grammar on the committed text before insertion: "new line", "new paragraph", "period/comma/question mark", "bullet"/"next bullet", "heading one", "scratch that" (delete last utterance), "stop dictation". Only treat them as commands when they are the entire utterance or trail a pause, to avoid eating the phrase "new line" in prose.

### Inserting into a textarea with native undo
- `document.execCommand('insertText', false, text)` on a focused textarea goes through the editing pipeline, fires `input`, and **is the way to get an entry on the native undo stack** in Chromium; it is deprecated on paper but has no replacement for this and works in Chrome, Safari and Firefox 89+ ([search summary of Firefox bug 1220696 and related threads](https://bugzilla-dev.allizom.org/show_bug.cgi?id=1220696)) [src, secondary]. Select a range first with `setSelectionRange(a, b)` to replace it.
- `setRangeText(text, start, end, 'end')` is a standard API, but in my recollection it **does not** record an undo step in Chromium [prior; one search summary claimed otherwise, so this is [unverified]: test it]. It does fire no `input` event by itself, so React state must be updated manually, which `apply()` does.
- Practical pattern: `insertAtCaret(el, text)` = `el.focus(); if (!document.execCommand('insertText', false, text)) { el.setRangeText(text, el.selectionStart, el.selectionEnd, 'end'); el.dispatchEvent(new Event('input', {bubbles: true})) }`. React's `onChange` fires from the `input` event so state stays in sync. Use the same helper for slash commands and paste.
- **Provisional text.** Do not put it in the textarea. Render it in the mirror layer at the caret index (wrap in a `<span class="tk-interim">` appended after the text before the caret), so it never touches the value, the undo stack, autosave, or revisions; the mirror already paints behind the textarea at identical metrics. Because the mirror must stay line-aligned, interim text must be single-line or its newlines must be accounted for in the textarea (reserve nothing; simply render interim after the caret line's text and accept it visually overlapping the following text for a moment, or show it in a floating pill at the caret position from the popup helper). The pill is simpler and robust: reuse `useCaretPopup`.
- **One utterance = one undo step.** Insert a committed chunk per final result; consecutive inserts in Chromium merge into one undo group if typed quickly, which is acceptable.
- **Caret robustness.** Capture `selectionStart` at record start; if the user moves the caret or types while dictating, re-anchor to the new caret and flush interim. Disable dictation while the editor is read-only or an assistant revision is pending review.

### Recommendation for Grain (Part B)
1. **Preview + durable split.** Keep 20 s durable segments as truth (make cuts VAD-aligned between about 8 and 25 s), add a volatile/final event stream for the live feel.
2. **Engine order for the stream:** (1) Apple Speech `SFSpeechAudioBufferRecognitionRequest`, on-device, partials on, fed from the existing AVAudioEngine tap, request rolled about every 50 s; (2) overlapping 8-10 s windows through `whisper-cli`/proxy with LocalAgreement-2; (3) later, sherpa-onnx or Moonshine v2 as an engine-independent streaming backend; (4) later, a Swift `SpeechAnalyzer` helper (NDJSON on stdout) on macOS 26+.
3. **Do not use `webkitSpeechRecognition`** in the renderer. Keep capture in the backend for session recording. For caret dictation use whichever path ends up with fewer moving parts; the backend tap is the smaller change because Apple Speech already lives there.
4. **Event schema** shared by all engines: `{session, kind: 'volatile'|'final', text, t0, t1}`, pushed over `/events`; the editor owns inserting finals (via `insertAtCaret`) and drawing volatile text in a caret pill.
5. **Two modes, one pipeline:** "Dictate at caret" (hold-to-talk chord, finals inserted into the note, no audio kept) and "Record session" (toggle, audio kept as segments with `retranscribe`, finals appended to a `## Transcript` section or a linked meeting row, with an optional "clean up into notes" action using the existing enhance prompt).
6. **Cleanup:** rule-based pass always, LLM pass optional and only on committed text; small command-word grammar applied only to whole utterances.
7. **Undo:** use `execCommand('insertText')` with a `setRangeText` fallback; verify both empirically in the packaged Electron before building on either.
8. **Things I could not verify:** whether `SpeechAnalyzer` has any ObjC projection usable from pyobjc; whether on-device `SFSpeechRecognizer` truly lifts the 1-minute cap; Moonshine's real-world English accuracy and licence for the exact model; main-window `media` permission handler state; `setRangeText` undo behaviour. Each is a short experiment, listed in that order of priority.

## Source list
- Apple: [SFSpeechRecognizer](https://developer.apple.com/documentation/speech/sfspeechrecognizer), [WWDC25 "Bring advanced speech-to-text to your app with SpeechAnalyzer"](https://developer.apple.com/videos/play/wwdc2025/277/)
- Python/Swift bridges: [pyobjc Speech notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Speech.html), [mac-speech-analyzer](https://pypi.org/project/mac-speech-analyzer/), [mac-transcriber](https://github.com/yrocaz/mac-transcriber)
- Engines: [whisper.cpp stream example](https://github.com/ggml-org/whisper.cpp/tree/master/examples/stream), [whisper_streaming](https://github.com/ufal/whisper_streaming), [WhisperKit / Argmax OSS](https://github.com/argmaxinc/WhisperKit), [parakeet-mlx](https://github.com/senstella/parakeet-mlx), [sherpa-onnx streaming models](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/index.html), [sherpa-onnx mic example](https://k2-fsa.github.io/sherpa/onnx/python/real-time-speech-recongition-from-a-microphone.html), [Moonshine](https://github.com/moonshine-ai/moonshine), [Moonshine v2 paper](https://arxiv.org/html/2602.12241v1)
- Electron: [systemPreferences](https://www.electronjs.org/docs/latest/api/system-preferences), [electron#7749](https://github.com/electron/electron/issues/7749), [chromium-dev webkitSpeechRecognition network error](https://groups.google.com/a/chromium.org/g/chromium-dev/c/RBZXqc0Zvmc), [missing audio-input entitlement reports](https://github.com/NousResearch/hermes-agent/issues/37718)
- Editor: [textarea-caret-position](https://github.com/component/textarea-caret-position), [Firefox bug 1220696 on execCommand insertText](https://bugzilla-dev.allizom.org/show_bug.cgi?id=1220696)
- Note-app feature survey: background knowledge, no pages fetched.
