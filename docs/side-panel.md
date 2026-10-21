# Side panel

Grain has one right-hand surface, built on a shared shell (`src/renderer/src/components/SidePanel.tsx`),
and this page covers both what lives in it and the separate show panel beside the transcript.

## The workspace panel

The workspace panel is the chat's side surface: a tab strip over one scrolling body.

- **Tabs** come from the `sidepanel.tab` slot, in slot order: Context, Files, Changes, Review, Traces,
  then tabs added by extensions, with Terminal last. A tab can hide itself (`shown`) and show a count.
  A draft chat with no id has only Context. The strip is a `tablist` (arrow keys, Home and End move
  between tabs); only the active tab's body is in the page.
- **One scroll area.** The active tab fills the panel's height and the body scrolls; the running
  checklist and the workers list lead the panel above the strip. The Files tab keeps its tree and
  preview split inside the body.
- **Width.** Resizable from its left edge, 300 to 720 pixels, default 380. The width is remembered per
  window (the `--side-panel-w` variable, stored by the resize handle). Dragging it well under the
  minimum closes it.
- **Narrow windows.** When the chat is 900 pixels wide or less the panel floats over the transcript
  instead of taking a column.
- **Opening.** In a chat, Cmd+I toggles the panel on the Context tab (Control+Command+I does the
  same, and the Workspace button in the title bar opens the same panel). Anywhere else Cmd+I opens
  the Page agent. A request for Traces, Terminal, Files or Review opens the panel on that tab, and a
  chat whose desk is in review opens on Review. While the panel stays open it remembers the tab you
  chose. Escape closes it.
- **Canvas chat windows** have the same panel: a Workspace panel button in the window's header opens it over that window's right edge, and Cmd+I (or Control+Command+I) toggles it for the focused chat window. Its tabs show that window's chat only.
- **Page agent** (Cmd+I outside a chat) uses the same shell and the same width. Under its header a
  "Sent" line shows the size of the page block (about one token per four characters)
  with a disclosure that reveals the exact text; it is absent when the screen sends nothing.
- **Extensions** add tabs by registering a `sidepanel.tab` slot item with a label and a component
  (and optionally an overlay drawn over the chat, as Terminal does for its window).

## The show panel

A panel beside the chat for content the assistant wants you to look at while it talks about it:
a full-page HTML mock-up, an SVG, a diagram, a chart, markdown, or a file on this Mac. It is per
chat (a chat window in a canvas gets its own), resizable from its left edge, and closes with the ×
or by dragging it shut.

## How the show panel opens

- **The `show` tool** (`backend/personal_os/tools.py`, group `utility`, always offered). The model
  passes `kind` (`html`, `svg`, `mermaid`, `chart`, `interactive`, `markdown` or `file`) with
  `content`, or `path` for a file, an optional `title`, and an optional `pane` (`left` or `right`, see
  Two panes).
- **"Open in side panel"** on the header of any ```` ```html ````, ```` ```svg ````,
  ```` ```mermaid ````, ```` ```chart ```` or ```` ```interactive ```` block in a reply
  (`src/renderer/src/components/ShowButton.tsx`).
- **"Open in side panel"** on the tool row of an earlier `show` call, after the panel was closed.

## Two panes

The panel remembers the last 8 things shown in the chat (in memory only, never persisted). The Split
button in its header opens a second pane; each pane then has a picker listing that history by title, so
two PDFs, or a PDF and a note, sit side by side. A divider between them drags like the panel's own edge.
A new show lands in the right pane while split; `pane: "left"` overrides it, and `pane: "right"` on a
single panel splits it. Closing a pane leaves the other one as the only pane. The state is
`src/renderer/src/lib/panelPanes.ts`, per chat in the store.

## The frame

Every pane has a slim header: title, then size and page count (PDFs), and the actions. A PDF still
renders in the built-in viewer in a blob frame, but on a page background that follows the theme, with the
viewer's toolbar and thumbnails off (`#toolbar=0&navpanes=0&view=FitH`); the toolbar button brings the bar
back. Files also get **Open in Finder** and **Open externally** (main process, `data:file-action`: the file
must be inside the home folder, and only documents and media open, never anything that could run).
Markdown and text read at a comfortable width (about 72ch).

## Live content

A file is read again when the pane is (re)opened, on **Refresh**, and when the window regains focus
(not for a PDF, which would reload the viewer under the reader). A chart or interactive spec from a tool
run has no source to re-read, so its header says when it was generated instead.

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
- `src/renderer/src/lib/showPanel.test.ts`, `panelPanes.test.ts`, `src/shared/openable.test.ts`, `src/main/navPolicy.test.ts`: file routing, the PDF fragment and header, the pane reducer, the blob rule.
- `tests/e2e/show-panel.spec.mjs`: the panel end to end, including a PDF in the viewer.
