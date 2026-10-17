"""The `grain` command: `grain ext new | check | dev | test | pack`."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

import httpx

from ..manifest import ManifestError, load_manifest
from . import pack as packing
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
    r = client.post(f"/dev/extensions/{ext_id}/reload")
    if r.status_code == 404:
        r = client.post("/dev/extensions/load", json={"path": str(root)})
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
    return _print(check(Path(args.folder).resolve(), Path(args.previous) if args.previous else None))


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
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"error: {getattr(e, 'stderr', None) or e}")
        return 1
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="grain")
    ext = top.add_subparsers(dest="group", required=True).add_parser("ext", help="write extensions").add_subparsers(dest="cmd", required=True)
    p = ext.add_parser("new", help="scaffold an extension")
    p.add_argument("id")
    p.add_argument("--profile", action="store_true", help="a profile: theme and defaults, no code")
    p.add_argument("--dir", default=".", help="where to create the folder")
    p.set_defaults(run=cmd_new)
    p = ext.add_parser("check", help="validate an extension folder; exits 1 on an error")
    p.add_argument("folder")
    p.add_argument("--previous", help="an earlier extension.json or extension.lock.json to diff the permissions against")
    p.set_defaults(run=cmd_check)
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
