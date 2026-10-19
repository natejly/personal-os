# LaTeX

A built-in extension for writing and compiling multi-file LaTeX documents. It starts off on a fresh install; turn it on under Settings > Sidebar or Settings > Advanced > Extensions.

## Where files live

A project is a plain folder, `~/Grain/LaTeX/<name>/` (override the root with `GRAIN_LATEX_DIR`). The project name is the folder name. A new project gets a `main.tex` from a minimal article template. Because the files are real, the chat's file tools read and edit the same ones the editor shows.

The main file is `main.tex`; if that is absent, the single top-level `.tex` containing `\documentclass`. A project can pin both the main file and the engine in `.grain-latex.json` (`{"engine":"pdflatex","main":"main.tex"}`).

Every file route resolves its path inside the project folder and refuses `..`, absolute paths, hidden names and symlinks that point outside. Text reads and writes are capped at 5 MB, uploads at 25 MB.

## Grain projects

A document can belong to a Grain project: `project_id` in `.grain-latex.json` (null means none). `POST /latex/projects` takes `{name, project_id?}`, `PATCH /latex/projects/{name}` moves a document, and `GET /latex/projects?project_id=` filters. An unknown project id is refused on write; a project that is later deleted just leaves its documents unlinked, with their files untouched.

A project's Files column lists its LaTeX documents (name, last change, whether a PDF exists) with a "New LaTeX document" action. Opening one edits it in place with the same editor, PDF preview and problems list as the LaTeX view. The extension fills the `project.files` slot, so it disappears with the view when LaTeX is turned off. The LaTeX view groups its document picker by Grain project and has a control to change a document's project.

## Compile

`latexmk -pdf|-xelatex|-lualatex -interaction=nonstopmode -halt-on-error -file-line-error -synctex=1 -outdir=.grain-build <main>`, run in the project folder with `openout_any=p` and never with shell escape. It times out after 120 s, one compile per project at a time. The result PDF is copied to `<main stem>.pdf` next to the sources. Errors and warnings are parsed from the log into file, line and message. TeX Live (or MacTeX) must be installed; `/Library/TeX/texbin` is added to the PATH.

## Agent tools

- `latex_projects` lists documents with their folders, main files and Grain project; an optional `project` (id or name) filters.
- `latex_new` creates a document from the template. `project` (id or name) is optional and defaults to the chat's own project.
- `latex_compile` compiles and returns errors with file and line, so the agent can fix them with the file tools and compile again.

## View

Files on the left, a CodeMirror editor in the middle (saves on Cmd+S and 1.5 s after typing stops; Cmd+Enter compiles), the PDF on the right. Clicking a problem opens its file at that line.
