# Artifacts and the canvas

Status: built. Charts and Mermaid stay inline; anything substantial the model writes now opens as a
live document on a canvas beside the thread, where it can be edited, versioned and popped out.

---

## 1. The protocol

The model emits a fenced `artifact` block whose **first line is a JSON header** and whose remainder
is the document:

````
```artifact
{"id": "expense-tracker", "title": "Expense tracker", "kind": "react"}
function App() { return <div className="p-6">…</div> }
```
````

| Field | Meaning |
|---|---|
| `id` | Short stable slug. Re-using it **re-versions the same artifact**; a new `id` starts a new one. |
| `title` | Shown on the card and the panel header. Falls back to the slug. |
| `kind` | `html` · `svg` · `react` · `markdown` · `code` (aliases: `jsx`/`tsx` → react, `md` → markdown). |
| `lang` | Highlight hint, `code` only. |

A header that will not parse is not fatal: the block is kept as a bare `html` document. An
**unterminated** block is dropped — half a document is not worth a row — but it still previews live
while the reply streams, because the renderer parses the partial block itself.

The instructions the model sees live in `RENDER_HINT` (`backend/personal_os/app.py`), right below
the existing `chart` and `mermaid` guidance.

## 2. Where the pieces live

```
backend/personal_os/artifacts.py    parser, storage, versioning, the HTML wrappers, the CSP
backend/personal_os/app.py          + /artifacts routes, capability tokens, save-on-done
backend/tests/test_artifacts.py     28 tests: parsing, wrappers, escaping, versioning, auth
src/renderer/src/lib/artifacts.ts   the same block parser, for live preview mid-stream
src/renderer/src/components/ArtifactBlock.tsx   the card in the transcript
src/renderer/src/components/CanvasPanel.tsx     the panel: preview, editor, history
src/renderer/src/store.ts           artifacts, artifactDrafts, canvas
```

Two tables, created on first import like every other repo here:

- `artifacts` — one row per `(conversation_id, identifier)`, holding the current content and version.
- `artifact_versions` — every version ever, including reverts. **Reverting appends**; history is
  never rewritten.

Artifacts are scoped to a conversation and cascade with it.

## 3. Lifecycle

1. **Streaming.** `ArtifactBlock` parses the partial block on every delta and calls
   `noteArtifact`, which registers a *draft* and — only while a reply is in flight — opens the
   canvas. Loading an old chat does not hijack the pane; the header button (⌘⇧C) does.
2. **Preview.** The draft is POSTed to `/artifacts/preview`, which stages it in a capped, 30-minute
   in-memory cache and returns a URL. Debounced at 900 ms while streaming, 450 ms while editing.
3. **Persisting.** When the reply finishes, the backend parses the *whole* text and upserts each
   block, then emits an `artifacts` SSE event. The renderer swaps drafts for the saved rows.
   Re-saving byte-identical content does not burn a version.
4. **Editing.** The Code tab is a plain editor. Saving PUTs the content and makes version *n+1*.
   History lists every version with who wrote it — `from the model` or `edited here`.

## 4. Why the preview is served, not inlined

A `srcdoc` iframe **inherits the embedding page's CSP**. The renderer runs under
`default-src 'self'`, which would kill every script an artifact needs. So artifacts are served as
real pages from the sidecar's own origin — the same reason dashboard widgets are — and the
renderer's CSP already allows `frame-src http://127.0.0.1:* http://localhost:*`.

`document()` wraps each kind:

- `html` — a complete document (`<!doctype>`/`<html>`) is passed through untouched; a fragment is
  wrapped with base styles and the Tailwind CDN.
- `svg` — centred in a wrapper.
- `react` — Babel standalone compiles the JSX in place against React 18 UMD. Imports are stripped
  (there is no module graph), `export default` is unwrapped, hooks are put in scope, and the
  component and its mount share one `<script>` because Babel evaluates each `text/babel` block
  separately.
- `markdown` / `code` — the panel renders these itself; the served page is the pop-out fallback.

## 5. Security

Artifact content is model-authored and may quote untrusted text a tool fetched, so the sandbox is
closed by default.

- **Sandbox**: `sandbox="allow-scripts allow-popups allow-modals allow-forms"` — no
  `allow-same-origin`. Verified in the running app: the renderer's `frame.contentDocument` reads
  `null` and reaching into `contentWindow` throws `SecurityError`, so nothing inside an artifact
  can touch the app's DOM, its storage, or the preload bridge that holds the sidecar token.
- **CSP** (`artifacts.CSP`, sent as a header on every render):
  `connect-src 'none'` and `img-src data: blob:`. **There is no way out**: no `fetch`, no `XHR`, no
  `<img>` beacon, no remote font. This is the same stance the transcript takes, where `SafeImage`
  downgrades remote images to links. Verified live: `fetch()` from inside an artifact rejects.
  Scripts are allowed only from the three exact CDN URLs the wrappers use, plus `cdn.tailwindcss.com`.
  This is also why the render hint tells the model to inline its data.
- **Auth**: `/artifacts/{id}/render` and `/previews/{id}/render` are the only unauthenticated
  routes, and only with a short-lived HMAC capability (`?t=…&e=…`, 12 h) bound to *that one*
  artifact id. The iframe is a separate origin and cannot send the app token; the same URL is what
  "Open in browser" hands to the system browser. Widgets use the blunter scheme — path-suffix
  exemption with no signature — so this is a step up, not parity.
- **Escaping**: `</script` and `<!--` in a React artifact are neutralised before the code reaches
  the wrapper's `<script>`; `markdown`/`code` pages are fully HTML-escaped.

Trade-off worth knowing: **artifacts cannot load remote images.** Charts must be inline SVG or CSS.
That is deliberate — an `<img src>` is a working exfiltration channel — but it is the first thing to
revisit if artifacts start feeling too constrained.

## 6. What is not here

- No canvas *window manager*. `docs/canvas-mode.md` is still a proposal; this is the artifacts
  panel from the roadmap, which that document's §1 pillar 4 would later host as a widget.
- Artifacts live in one conversation. There is no cross-chat artifact library yet, though
  `GET /artifacts` already returns everything and takes a `project_id`.
- Pop-out is "open in the system browser", not a real Electron window.
