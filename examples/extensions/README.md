# Example extensions

Small extensions to copy from or install.

- `reading-list/` saves links with a note (tools only, `db:own`).
- `catalog.json` is an index in the format of the `extensionCatalogUrl` setting: `{"extensions": [{id, name, description, url, ref?, subdir?, author, verified}]}`. Entries appear under Available in Settings > Extensions.

## Install

Settings > Extensions > Install from URL or folder. Enter the git URL `https://github.com/natejly/personal-os.git`, ref `main` and subdirectory `examples/extensions/reading-list`, or pick a local folder. Grain fetches into quarantine without running anything, shows the permissions and a digest, and installs the extension disabled after you confirm. Enable it afterwards.

## Check and test

From the repository root, with the backend virtualenv:

```
cd backend && PYTHONPATH=. .venv/bin/python -m personal_os.extensions.authoring.cli ext check ../examples/extensions/reading-list
cd backend && PYTHONPATH=. .venv/bin/python -m personal_os.extensions.authoring.cli ext test ../examples/extensions/reading-list
```

`check` validates the manifest, permissions and imports; `test` runs the extension's `tests/` in the in-process harness with a scripted model.
