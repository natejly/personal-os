# Help and keyboard shortcuts

One dialog, `HelpOverlay`, with two sections and one search box.

- **Shortcuts**: every entry in `src/shared/shortcuts.ts`, grouped by its `group`, with keys shown as macOS glyphs (`formatAccelerator`). The OS-wide shortcuts (gather, quick capture, quick ask) and the dictation chord show the live, user-chosen keys, not the defaults.
- **Using Grain**: the `## ` sections of the built-in `grain-guide` skill (`backend/personal_os/guide.py`), read through `GET /skills`. The assistant answers "how do I" questions from the same text, so the dialog and the assistant say the same thing.
- Footer: **Ask Grain how to…** (opens Chats with "In Grain, how do I " in the composer), **Open logs**, **Settings**.

## Opening it

- ⌘/ (Help → Keyboard Shortcuts…), or ? when no text field has focus.
- Help → Using Grain opens it on the guide.
- Command palette: "Help and keyboard shortcuts", "Using Grain".
- Settings → Behavior → Shortcuts → Show all shortcuts; Settings → Data → Support → Help.

## The registry

`SHORTCUTS` is plain data: `{ id, label, keys, group, action?, scope }`.

- `menu` / `window`: an app-menu accelerator. `buildMenu()` builds these items with `item(id)`, which takes the label, accelerator and action from the registry; `window` actions go to the focused window.
- `component`: a key a component handles itself (composer, note editor, Spaces plane). Listed only. `plan-mode` is also shown in the View menu without being registered, so the menu cannot swallow it.
- `global`: OS-wide, set in Settings. `keys` is the default.

`src/shared/shortcuts.test.ts` fails if `src/main/index.ts` carries a literal accelerator, if a menu entry is missing from the menu, if two menu accelerators collide, or if ids repeat. When you add a shortcut, add it to the registry and to the "Keyboard shortcuts" section of `guide.py`.
