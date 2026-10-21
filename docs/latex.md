# LaTeX

A built-in extension that makes LaTeX sources ordinary Files. It has no view or sidebar row of its own: Files is its home. It starts off on a fresh install and for a built-in added after an install first recorded its extensions (`enabled: false` puts `latex` in `hiddenViews`, in the same way as every other built-in); an install with no `extensions-seeded.json` yet keeps it as it was. Turn it on under Settings > Extensions.

## Storage

A LaTeX source is a Files doc (the `docs` table) whose title ends in `.tex`, `.bib`, `.sty` or `.cls`. The suffix is the type: there is no kind column, and the same Files tree, search, history, pending-edit review and project scopes apply. Markdown-only processing (tags, wiki links) skips these titles.

A LaTeX project is a Files folder, in Personal or in a Grain project. Its main file is `main.tex`, or the one `.tex` holding `\documentclass`. Subfolders are relative paths for `\input`. A `.tex` outside any such folder is its own one-file project.

Images and other binary files for `\includegraphics` are not docs. "Upload files" in the editor files a LaTeX source (`.tex/.bib/.sty/.cls`, UTF-8 or latin-1) as a doc in the project folder, replacing a same-named doc only while it is the untouched new-project template (otherwise it arrives as `<name> (uploaded).tex`); sources an older version left in the asset folder become docs the next time the project is opened. Everything else is kept in the extension's data folder (`ext/latex/assets/<project key>/`), listed in the editor's file list, uploaded and removed there. Per-project settings (the engine) are in the extension-owned table `latex_projects`, keyed by the Grain project and the folder path; renaming the folder starts it from the defaults again.

## Editor

Opening a LaTeX source in Files shows the LaTeX editor instead of the markdown one: the project's files on the left (click to switch), CodeMirror in the middle, the PDF on the right, compile (Cmd+Enter), engine select, a problems list that opens the right file at the right line, and a button to save the PDF. Text saves through the normal doc routes, so autosave, history and conflict handling are the note ones. Assistant edits arrive as pending revisions shown with the usual diff and accept or reject. With the extension off, a `.tex` doc opens as a plain note.

The assistant revises a LaTeX file with `doc_edit` and checks it with `latex_compile`. "New LaTeX project" is in Files (the + menu, a project's menu and its Files column): it makes a folder with a `main.tex` from a minimal article template.

## Compile

`POST /latex/compile {doc_id, engine?}` copies every LaTeX source in the project folder (relative paths from the folder) and its assets into a build folder under the extension data folder, then runs `latexmk -norc -no-shell-escape -pdf|-xelatex|-lualatex -interaction=nonstopmode -halt-on-error -file-line-error -synctex=1 -outdir=.grain-build <main>` there with `openout_any=p`. A project rc file is never read. A doc title that contains a slash, starts with `.` or `-`, or would leave the build folder is refused. It times out after 120 s, one compile per project at a time. `GET /latex/pdf?doc_id=` serves the result. Errors and warnings are parsed from the log into file, line and message, and each error carries the id of the doc it came from. TeX Live (or MacTeX) must be installed; `/Library/TeX/texbin` is added to the PATH.

## Agent tools

- `latex_projects` lists LaTeX project folders with their Grain project and main doc id; an optional `project` filters.
- `latex_new` makes a folder with a `main.tex`; `project` (id or name) defaults to the chat's project.
- `latex_compile` takes any file id of the project (or a folder and project) and returns errors with doc id, file and line.

Edit with the doc tools. `doc_create` with a `.tex` title files a LaTeX source with its body untouched.
