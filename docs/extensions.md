# Extensions

Grain's features are built on an extension host. A feature is a folder with a manifest (`extension.json`), a backend half (`extension.py`) and, when it has UI, a renderer half (`extension.tsx`). The host loads the folder, wires what the manifest declares, and hands the code a scoped `api` object. The full design, decisions and as-built notes are in `docs/research/extensions-and-hooks.md`; the hook catalog is in `docs/extension-hooks.md`.

## What was built

Backend, under `backend/personal_os/extensions/`:

- Host: manifest loader and validator (`manifest.py`), hook bus (`hooks.py`), disposables, lifecycle, lazy activation on view or command, permission checks, scoped database (`scopeddb.py`), per-extension data folder, settings, secrets, jobs, notes and trash kinds on `api`.
- Hook catalog: one registry (`catalog.py`) that generates the tables in `docs/extension-hooks.md`, the Python SDK types and the TypeScript types. `grain ext gen-hooks --check` and `grain ext check --catalog` fail on drift.
- Seams: every tool call goes through `dispatch_tool` (`tool.pre`, `tool.result`, `tool.error`, `tool.post`, `tool.taint`); context contributors (`context.contribute`); post-turn subscribers (`turn.settled`, `reply.complete`, `run.after`); model, approval, memory, compaction, job and channel hooks; the planned hooks `turn.accept`, `turn.start`, `tools.offer`, `worker.report`, `file.written`, `review.accepted` and `notify`.
- Core guards (`guards.py`) run before any extension gate; extensions can only narrow `tool.permission`.
- Permission presets with scoped overrides and a loosening gate; the effective configuration (`grain config dump`).
- Runtime slots: swappable model client, router, tool source and sandbox backend, selected per chat, project, profile or globally, with a refusal-credentials breaker and a Needs-you card when a selection falls back.
- Swappable agent loop (`agent.loop`) with a shipped plan, execute and verify loop.
- Built-in extensions: `health`, `todos`, `telegram`, `google` (service), `calendar`, `mail`, `spaces`, `coding`, `memory-tree` (the History digest, see `docs/memory-tree.md`), and the `lean` and `focus` profiles.
- Review: an agent can draft an extension with the author skill and `ext_propose`; it lands disabled in Settings > Advanced > Extensions with its permission diff, findings and digest. Accepting names the digest the user reviewed; enabling is a separate step.

Renderer, under `src/renderer/src/extensions/`: an open view union and registries for sidebar, composer, message, side-panel, titlebar and status-bar slots, palette entries, keybindings, selection menus, fences, tool cards and recap sections; a per-extension store slice; design tokens and profile layouts.

## Enable an extension

Built-in extensions are listed in Settings > Advanced > Extensions and in the view toggles. A manifest's `enabled` field is its default; the user's choice wins. Disabling unloads the extension's handlers and tools for the next turn (a turn in flight keeps the tools it was offered). Installed folder extensions live in the installed folder of the data directory, load disabled, and run only after the user enables them.

## Author an extension

```
grain ext new my-ext            # manifest, extension.py, extension.tsx, a test, migrations/
grain ext new my-look --profile # theme tokens and defaults, no code
grain ext dev my-ext            # reload in a running dev backend on every edit
grain ext check my-ext          # manifest, apiVersion window, id and tool collisions, undeclared hooks,
                                # forbidden imports, tool-schema lint, authority-claim lint, permission diff
grain ext test my-ext           # in-process harness with a scripted model
grain ext pack my-ext           # check, bundle the renderer half, write the lock file
```

The manifest declares tools, views, slots, hooks, permissions (`db:own`, `context:volatile`, `hooks:gate`, `service:*`, and so on), requirements, provided services, owned tables and migrations. Hooks the code subscribes to must be declared in the manifest. Settings fields use the JSON Schema subset type, title, description, default, enum, minimum and maximum. `apiVersion` older than the supported window warns; newer is refused.

## Deferred

- Loading third-party extensions from a registry, pinning and signing (out of scope).
- Frontend hot reload with registry disposal and a packed renderer half for folder extensions (needs the renderer to load code from a folder); built-ins use Vite hot reload.
- Embeddings, images and audio through `model.client`; a read-only mode and denied-read probe on the default sandbox adapter.
- Hooks not built: `input.attachment`, `schedule.tick`, `context.history`, `text.delta`, `turn.continue`, `tool.args`.
- Extension proposals still use their own review list rather than the document-revision and desk-output paths.
- Per-step before and after cold-start and cache-hit measurements; end to end the cold start is unchanged and resident memory grew by about 6 percent.
