"""The `grain` command: `grain ext new | check | dev | test | pack | gen-hooks` and `grain config dump`."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

import httpx

from .. import hookgen
from ..manifest import ManifestError, load_manifest
from . import hookscan, pack as packing
from .check import CODE_EXCLUDED, check
from .scaffold import scaffold

POLL_S = 0.5


def snapshot(root: Path) -> dict[str, int]:
    """Modification time of every source file, to notice an edit."""
    return {p.as_posix(): p.stat().st_mtime_ns for p in root.rglob("*")
            if p.is_file() and not CODE_EXCLUDED & set(p.relative_to(root).parts) and p.name != packing.LOCK}


def push(client: httpx.Client, root: Path) -> str:
    """Tell the running dev backend to load this folder, or to reload it when it already has. Returns what to print."""
    try:
        ext_id = load_manifest(root / "extension.json").id
    except ManifestError as e:
        return f"error: {e}"
    try:
        r = client.post(f"/dev/extensions/{ext_id}/reload")
        if r.status_code == 404:
            r = client.post("/dev/extensions/load", json={"path": str(root)})
    except httpx.TransportError as e:
        return f"error: cannot reach the dev backend: {e}"
    if r.is_success:
        return f"{ext_id}: loaded ({len(r.json()['hooks'])} hook handler(s), {len(r.json()['tools'])} tool(s))"
    return f"{ext_id}: {r.json().get('detail', r.text)}"


def _client(args: argparse.Namespace) -> httpx.Client:
    token = args.token or os.environ.get("PERSONAL_OS_AUTH_TOKEN")
    data_dir = args.data_dir or os.environ.get("PERSONAL_OS_DATA_DIR")
    if not token and data_dir and (path := Path(data_dir) / ".auth_token").is_file():
        token = path.read_text("utf-8").strip()
    if not token:
        raise SystemExit("no auth token: pass --token, or --data-dir of the dev backend (PERSONAL_OS_DATA_DIR)")
    return httpx.Client(base_url=args.url, headers={"x-personal-os-token": token}, timeout=30)


def _print(findings: Sequence[Any]) -> int:
    for f in findings:
        print(f)
    errors = sum(f.level == "error" for f in findings)
    print(f"{errors} error(s), {sum(f.level == 'warn' for f in findings)} warning(s)")
    return 1 if errors else 0


def cmd_new(args: argparse.Namespace) -> int:
    try:
        root = scaffold(args.id, Path(args.dir), args.profile)
    except (ValueError, FileExistsError) as e:
        print(f"error: {e}")
        return 1
    print(f"created {root}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    if args.catalog:
        problems = hookscan.catalog_problems()
        print("\n".join([*problems, f"{len(problems)} problem(s) in the hook catalog"]))
        return 1 if problems else 0
    if not args.folder:
        raise SystemExit("grain ext check: give a folder, or --catalog")
    return _print(check(Path(args.folder).resolve(), Path(args.previous) if args.previous else None))


def cmd_gen_hooks(args: argparse.Namespace) -> int:
    return hookgen.main(["--check"] if args.check else [])


def cmd_dev(args: argparse.Namespace) -> int:
    """Watch the folder and reload it in the running dev backend (started with GRAIN_EXT_DEV=1) on every edit."""
    root = Path(args.folder).resolve()
    with _client(args) as client:
        seen = snapshot(root)
        print(push(client, root))
        try:
            while True:
                time.sleep(POLL_S)
                if (now := snapshot(root)) != seen:
                    seen = now
                    print(push(client, root))
        except KeyboardInterrupt:
            return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Run the extension's own tests against a scratch data directory."""
    root = Path(args.folder).resolve()
    if not (root / "extension.json").is_file():
        print(f"error: cannot read {root / 'extension.json'}")
        return 1
    if not args.pytest_args and not (root / "tests").is_dir():
        print(f"{root.name} has no tests directory: nothing to run")
        return 0
    with tempfile.TemporaryDirectory(prefix="grain-ext-test-") as scratch:
        backend = str(Path(__file__).parents[3])  # where personal_os and grain_sdk are, installed or not
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [backend, os.environ.get("PYTHONPATH")])),
               "PERSONAL_OS_DATA_DIR": scratch, "GRAIN_SECRETS_BACKEND": "file",
               "PERSONAL_OS_OPENCODE_DB": str(Path(scratch) / "no-opencode.db")}  # keeps the run away from the real coding-agent usage database
        targets = args.pytest_args or [str(root / "tests")]
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets], cwd=root, env=env).returncode


def cmd_pack(args: argparse.Namespace) -> int:
    root = Path(args.folder).resolve()
    findings = check(root)
    if any(f.level == "error" for f in findings):
        return _print(findings)
    try:
        print(f"wrote {packing.pack(root)}")
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as e:
        print(f"error: {getattr(e, 'stderr', None) or e}")
        return 1
    return 0


def cmd_config_dump(args: argparse.Namespace) -> int:
    """The settings in force, with the source of each value, read from a data folder with the server stopped or running.
    Opens the database read-only: no migrations, no recovery, nothing written."""
    from ... import config, provider_keys, provider_route
    from ...db import ReadOnlyDatabase, data_dir_from_env
    from ...repos import Conversations, Projects
    from ...runtime.defaults import core_providers
    from ...runtime.routes import Selections
    from ...runtime.slots import Runtime
    from ...tools import Toolbox
    from ..host import BUILTIN_ROOT
    from ..hooks import HookBus
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else data_dir_from_env()
    if not (data_dir / "personal-os.db").exists():
        print(f"error: no Grain database in {data_dir}")
        return 1
    db = ReadOnlyDatabase(data_dir)
    convos, projects = Conversations(db), Projects(db)
    if args.project and projects.get(args.project) is None:
        print(f"error: no such project: {args.project}")
        return 1
    if args.chat and convos.get(args.chat, with_messages=False) is None:
        print(f"error: no such chat: {args.chat}")
        return 1
    provider_route.set_key_source(lambda: provider_keys.load(db.secrets))
    declared = config.declared_settings(load_manifest(p) for p in sorted(BUILTIN_ROOT.glob("*/extension.json")))
    runtime = Runtime(HookBus(), core_providers())  # the core's providers only: extension code is not loaded here
    scopes = Selections(db, convos, projects).scopes(args.chat, args.project)
    dump = config.effective(db, Toolbox(None, None, None, lambda: config.assemble(db)), declared,  # type: ignore[arg-type]
                            project_id=args.project, conversation_id=args.chat,
                            runtime={slot: r.to_dict() for slot, r in runtime.resolve_all(scopes).items()})
    print(json.dumps(dump, indent=2, ensure_ascii=False, default=str) if args.json else config.render(dump))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="grain")
    groups = top.add_subparsers(dest="group", required=True)
    cfg = groups.add_parser("config", help="inspect the settings").add_subparsers(dest="cmd", required=True)
    p = cfg.add_parser("dump", help="print the settings in force and where each value came from")
    p.add_argument("--json", action="store_true")
    p.add_argument("--project", help="a project id: its overrides apply")
    p.add_argument("--chat", help="a chat id: its overrides apply")
    p.add_argument("--data-dir", help="the data folder (default $PERSONAL_OS_DATA_DIR)")
    p.set_defaults(run=cmd_config_dump)
    ext = groups.add_parser("ext", help="write extensions").add_subparsers(dest="cmd", required=True)
    p = ext.add_parser("new", help="scaffold an extension")
    p.add_argument("id")
    p.add_argument("--profile", action="store_true", help="a profile: theme and defaults, no code")
    p.add_argument("--dir", default=".", help="where to create the folder")
    p.set_defaults(run=cmd_new)
    p = ext.add_parser("check", help="validate an extension folder, or the hook catalog; exits 1 on an error")
    p.add_argument("folder", nargs="?")
    p.add_argument("--previous", help="an earlier extension.json or extension.lock.json to diff the permissions against")
    p.add_argument("--catalog", action="store_true", help="check the hook catalog against every call site and the generated files, not a folder")
    p.set_defaults(run=cmd_check)
    p = ext.add_parser("gen-hooks", help="regenerate the hook tables, SDK types and TypeScript types from the hook catalog")
    p.add_argument("--check", action="store_true", help="exit 1 if a generated file is out of date, change nothing")
    p.set_defaults(run=cmd_gen_hooks)
    p = ext.add_parser("dev", help="reload the folder in a running dev backend on every edit")
    p.add_argument("folder")
    p.add_argument("--url", default=os.environ.get("PERSONAL_OS_BACKEND_URL", "http://127.0.0.1:8765"))
    p.add_argument("--token")
    p.add_argument("--data-dir", help="the dev backend's data directory, where its auth token is")
    p.set_defaults(run=cmd_dev)
    p = ext.add_parser("test", help="run the extension's tests")
    p.add_argument("folder")
    p.add_argument("pytest_args", nargs="*")
    p.set_defaults(run=cmd_test)
    p = ext.add_parser("pack", help="check, bundle the renderer half and write the lock file")
    p.add_argument("folder")
    p.set_defaults(run=cmd_pack)
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
