# Saved dashboards

A dashboard is a normal file in a `Dashboards` folder (Personal or a project), so it is backed up to Drive,
keeps a revision history, and is edited through the same accept/reject diff as any other file.
Its content is a JSON envelope; `saved_dashboards.py` owns the format.

```json
{
  "grainDashboard": 1,
  "format": "spec",
  "mode": "live",
  "bindings": [
    {"name": "food", "source": "doc", "doc": "Daily Food Log"},
    {"name": "steps", "source": "health", "metric": "steps", "days": 30, "provider": "coros"}
  ],
  "views": [{"kind": "chart", "spec": {"type": "line", "x": "date", "series": ["kcal"], "data": "$food"}}]
}
```

## Formats

- `spec` (default): 1 to 12 `views`, each a `chart` or `interactive` spec, the same JSON the chat fences render. A spec whose
  `data` is `"$name"` gets that binding's rows when it opens. Inline data works too.
- `html`: an `html` string for layouts a spec cannot express. The app fills it on every open: `{{name}}` becomes a table of
  the binding's rows, `{{name.field}}` the escaped value of `field` in its last row, anything unknown becomes empty.
  Scripts do not run in the preview, so do not add any.

## Bindings

Bindings are references, never copies.

- `doc`: a file by title or id. Rows are a JSON array, a JSON object's `rows` array (how a skill-owned JSON database is
  stored), or else the first markdown table, with numeric cells as numbers.
- `health`: readings of one `metric` over the last `days` (default 30), as `{date, value}`. `provider` keeps only readings a
  connected source synced (for example `coros`).

## Live and snapshot

- `live` (default): every open re-reads every binding. The rows that worked are remembered (`dashboard_binding_cache`); when a
  binding later fails, its last good rows are shown and the failure is reported by binding name.
- `snapshot`: saving reads every binding once and writes the rows into the file. Opening never reads a binding or writes
  anything.

## Using them

- Routes: `POST /saved-dashboards`, `GET /saved-dashboards`, `GET /saved-dashboards/{id}/render`, `POST /saved-dashboards/{id}/pin`.
- Tools: `dashboard_save`, `dashboard_open` (shows it inline), `dashboard_pin`. To change one, the assistant uses `doc_edit`
  on its file; an edit that would break the envelope is refused.
- `@name` in the composer, where name is the title lowercased with runs of other characters turned into `-` (the same rule
  as chats), tells the assistant which dashboard is meant.
