# Feature modules — pilot contract (todos)

Status: **FROZEN 2026-10-01** for the pilot, since merged to `main` (todos is the ported module).

Goal: one unit per feature that owns its routes, tools, background loops, Today payload, view, nav entry,
canvas widget and Today card, so the shell iterates a list instead of naming each feature. Todos is the
pilot because it touches every surface. Built-in only — no runtime loading, no third-party code.

## Contract files (written by the contract owner; nobody else edits them)

| File | What |
|---|---|
| `backend/personal_os/modules/__init__.py` | `ModuleContext`, `Module` base, `build_modules`, `get` |
| `src/renderer/src/shell/types.ts` | `ModuleDef`, `ModuleView`, `ModuleNav`, `ModuleHome` |
| `src/renderer/src/shell/registry.ts` | `MODULES`, `moduleForView`, `moduleHome` |
| `docs/module-manifest.md` | this file |

## Behaviour that must not change

- Every HTTP path, method, request body and response shape: `/todos*`, `/integrations/google/tasks-sync*`,
  `/integrations/google/todo-calendar*`. `/integrations/google/tasklists` stays in app.py (Google listing).
- `GET /dashboard` still returns `todos` (open, first 12) and `todo_stats`.
- Tool names, schemas, descriptions, examples, groups, danger levels, **and their position in
  `toolbox.specs`** (todo tools register at the same point, between `todo_write`'s working group and the modules after it).
- `todos.on_change` still pokes both Google loops; both loops start on startup and are cancelled on shutdown.
- The sidebar order, the Today card order, the Settings → Modules toggles and their settings keys
  (`homeWidgets.todos`, `hiddenViews: ['todos']`), the badge count, the canvas widget and its drag kind.

## Slices and ownership (exclusive — if you need a change in a file you do not own, put it in your report)

### backend — `TodosModule`
| Path | Note |
|---|---|
| `backend/personal_os/modules/todos.py` | new. `TodosModule(Module)`, `key="todos"`. Owns `self.store = Todos(ctx.db)`, `self.tasks_sync`, `self.calendar_mirror`, the `on_change` wiring, the router (`TodoIn`/`TodoPatch` + the 4 todo routes + tasks-sync and todo-calendar routes and their models), the 4 todo tools (moved verbatim from `tools._register_todos`), start/stop of both loops, `today()`. |
| `backend/personal_os/app.py` | delete the moved code; `modules = build_modules(ModuleContext(...))` right after `google`; keep a module-level alias `todos = modules_get(modules, "todos").store` (and `tasks_sync`, `todo_calendar` aliases) for the remaining readers; include each router; one startup/one shutdown hook looping the modules; `/dashboard` merges `m.today()`; pass `modules=modules` to Toolbox. |
| `backend/personal_os/tools.py` | drop the `todos=` param, `self.todos` and `_register_todos`; add `modules: list[Any] \| None = None`, stored as `self.modules`, and call `m.register_tools(self)` at the spot `_register_todos` ran; `available()` consults `m.tool_available(name)` for any module that returns non-None. |
| `backend/tests/test_modules.py` | new. Tests: tool order/names unchanged; routes reachable via `TestClient`; `/dashboard` keeps `todos`/`todo_stats`; `on_change` pokes; start/stop leave no running tasks. |

### frontend-module — the todos `ModuleDef`
| Path | Note |
|---|---|
| `src/renderer/src/features/todos/module.tsx` | new. `export const todosModule: ModuleDef`: `view: { id: 'todos', Component: TodosView, optional: true }`, `nav: { section: 'apps', order: 0, badge: s => s.dashboard?.todo_stats?.open ?? null }` (an `apps` entry sits in the title-bar app strip, not the sidebar), `widget` = the existing `def` from `canvas/widgets/todos`, `home: { key: 'todos', label: 'Todos', Card: TodosCard }`. |
| `src/renderer/src/features/todos/TodosCard.tsx` | new. The Today card, moved verbatim out of `HomeView.tsx` (the `<section className="widget">` at ~line 147). |

`TodosView.tsx`, `TodoItem.tsx` and `canvas/widgets/todos.tsx` do **not** move in the pilot.

### frontend-shell — consumers read `MODULES`
| Path | Note |
|---|---|
| `src/renderer/src/App.tsx` | replace `view === 'todos' && <TodosView />` with a render of `moduleForView(view)?.view?.Component` (only for module-owned views). |
| `src/renderer/src/components/Sidebar.tsx` | drop the todos `NAV` row and the `todos` branch of `libCount`; merge module nav entries into `NAV`/`KNOWLEDGE` by `order` (shell rows get 0,10,20,… in their current order). Badge comes from `nav.badge`. |
| `src/renderer/src/components/HomeView.tsx` | the todos `<section>` becomes `on(key) && <Card data={d} />` for the module whose `home.key` is `'todos'`, **at the same position**. |
| `src/renderer/src/modules.ts` | `HOME_MODULES`/`OPTIONAL_VIEWS` keep their order; the todos rows come from `MODULES` (same key, label, view id). |
| `src/renderer/src/canvas/registry.ts` | `WIDGETS.todos` comes from the module's `widget`. Keep `Record<WidgetKind, WidgetDef>`. |
| `src/renderer/src/shell/registry.test.ts` | new, add to `npm test` list in `package.json`: every module's `view.id`/`widget.kind`/`home.key` is unique and matches the shell's lists. |
| `package.json` | only the `test` script line. |

## Amendments after integration

- `modules.get(modules, key, cls)` takes the concrete class and returns it typed, so app.py's aliases need no ignores.
- `homeModuleOn`/`viewHidden` live in the leaf `src/renderer/src/moduleToggles.ts` (re-exported from `modules.ts`).
  `store.ts` imports them, and `modules.ts` now imports the catalog, whose views import the store: going
  through `modules.ts` made a cycle that would throw on any entry that loads the catalog before the store.
- `test_mcp_servers` scans `modules/*.py` too, so tools that move into a module stay covered by the
  reserved-name check.

## Later (not this pilot)
Open the `View`/`WidgetKind` unions; move store slices into modules; a settings-panel slot (GoogleSettings'
sync UI); per-module SCHEMA registration; port calendar, docs, mail; then think about third-party.
