# Reading list

A small example extension. It adds two tools:

- `reading_list_add(url, note)` saves an http or https link (up to 2000 characters) with a note (up to 500 characters).
- `reading_list_list(limit)` lists saved links, newest first (default 20, at most 100).

It is backend-only: no views, no settings, no recap section. Its only permission is `db:own`, a private table (`reading_list_links`) created by `migrations/0001_init.sql`. It makes no network requests; links are stored, never fetched.

## Install

In Grain open Settings > Extensions > Install from URL or folder and give either:

- the git URL `https://github.com/natejly/personal-os.git`, ref `main`, subdirectory `examples/extensions/reading-list`; or
- a local folder path to this directory.

Review the consent screen, confirm, then enable the extension. It installs disabled.

## Develop

```
grain ext check examples/extensions/reading-list
grain ext test examples/extensions/reading-list
```
