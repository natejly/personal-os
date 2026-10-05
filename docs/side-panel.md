# Side panel

A panel beside the chat for content the assistant wants you to look at while it talks about it:
a full-page HTML mock-up, an SVG, a diagram, a chart, markdown, or a file on this Mac. It is per
chat (a chat window in a Space gets its own), resizable from its left edge, and closes with the ×
or by dragging it shut.

## How it opens

- **The `show` tool** (`backend/personal_os/tools.py`, group `utility`, always offered). The model
  passes `kind` (`html`, `svg`, `mermaid`, `chart`, `interactive`, `markdown` or `file`) with
  `content`, or `path` for a file, and an optional `title`.
- **"Open in side panel"** on the header of any ```` ```html ````, ```` ```svg ````,
  ```` ```mermaid ````, ```` ```chart ```` or ```` ```interactive ```` block in a reply
  (`src/renderer/src/components/ShowButton.tsx`).
- **"Open in side panel"** on the tool row of an earlier `show` call, after the panel was closed.

## What the model sees

The content never goes back to the model. `app.py` pops the `show` key off the tool result before
the preview is summarised, the same side channel `images` uses, and the model gets a one-line
receipt (`shown`, `note`). The payload rides on the tool event instead, so it is persisted with the
message and a replayed reply can reopen the panel. Inline content is capped at 200k characters;
a file at 50 MB.

## Rendering and trust

The panel (`src/renderer/src/components/ShowPanel.tsx`) reuses the reply's own block renderers, so
the boundary is the one the inline blocks already have: model HTML and SVG run in the sandboxed
`srcdoc` frame (`lib/htmlFence.ts`, `allow-scripts` and never `allow-same-origin`), mermaid renders
in strict mode, chart specs go through the parser.

A file is fetched from `GET /local/raw?path=` with the app token, under the same guard as
`read_local_file` (`mac.allowed_path`: the home folder only, no hidden folders, no `~/Library`,
never the app's own data). The route serves HTML, SVG, XML and JavaScript as `text/plain`, so a
file of the user's is never a page in the app's origin either; the panel routes it by name into
the sandboxed frame (`lib/showPanel.ts`). A PDF becomes a `blob:` URL in an `<iframe>`, which the
built-in viewer renders; the main process's frame-navigation guard (`src/main/navPolicy.ts`) admits
only `blob:` URLs of the renderer's own origin, so a blob made inside a sandboxed preview
(`blob:null/…`) is still refused. Images are shown as images; text files as a fenced block;
anything else gets a "Save a copy" button.

## Tests

- `backend/tests/test_show.py`: the tool's payload and errors, the route's guard and content types.
- `src/renderer/src/lib/showPanel.test.ts`, `src/main/navPolicy.test.ts`: file routing and the blob rule.
- `tests/e2e/show-panel.spec.mjs`: the panel end to end, including a PDF in the viewer.
