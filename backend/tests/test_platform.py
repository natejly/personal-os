"""The platform layer (osplat): the interfaces' shared contract, the pure macOS parts, and the rule that nothing outside the
package reads the operating system directly. Runs on any host; the macOS pieces are text and argv, never executed.

Run: python backend/tests/test_platform.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="platform-"))

from personal_os import osplat  # noqa: E402
from personal_os.osplat import base, darwin, linux, win32  # noqa: E402

NAMES = ("darwin", "linux", "win32")
CAPABILITY_KEYS = {"secrets_keychain", "sandbox_shell", "sandbox_python", "open_path", "reveal_path", "open_url", "file_search",
                   "shortcuts", "screenshot", "screen_recording", "frontmost_app", "full_disk_access", "speech_recognition",
                   "memory_probe", "process_groups"}


# ---- shared contract: every implementation, built on any host ----
@pytest.mark.parametrize("name", NAMES)
def test_every_platform_fills_every_interface(name: str) -> None:
    plat = osplat._build(name)
    assert plat.name == name
    assert isinstance(plat.paths, base.Paths) and isinstance(plat.secrets, base.Secrets)
    assert isinstance(plat.sandbox, base.Sandbox) and isinstance(plat.desktop, base.Desktop) and isinstance(plat.proc, base.Proc)


@pytest.mark.parametrize("name", NAMES)
def test_paths_contract(name: str) -> None:
    p = osplat._build(name).paths
    for d in (p.data_dir(), p.logs_dir(), p.work_folder(), p.trash_dir()):
        assert isinstance(d, Path) and d.is_absolute()
    assert p.work_folder() == Path(os.path.expanduser("~")) / "Grain"
    assert p.keychain_dirs() and all(not k.startswith("/") for k in p.keychain_dirs())
    assert all(k.startswith("/") for k in p.system_keychains())
    assert isinstance(p.app_bundle(), str) and p.app_bundle()
    assert not p.browser_support_dir().startswith("/")


@pytest.mark.parametrize("name", NAMES)
def test_capability_map_has_the_same_keys_everywhere(name: str) -> None:
    caps = osplat._build(name).capabilities()
    assert set(caps) == CAPABILITY_KEYS and all(isinstance(v, bool) for v in caps.values())


@pytest.mark.parametrize("name", NAMES)
def test_secrets_contract(name: str) -> None:
    s = osplat._build(name).secrets
    assert s.store_name() == ("keychain" if s.keychain_available() else "file")
    assert s.keychain_available() is (name == "darwin")


@pytest.mark.parametrize("name", NAMES)
def test_sandbox_contract(name: str) -> None:
    sb = osplat._build(name).sandbox
    assert isinstance(sb.name, str)
    if name == "darwin":
        assert sb.wrap(["echo", "hi"], "(version 1)") == ["sandbox-exec", "-p", "(version 1)", "echo", "hi"]
    else:
        assert not sb.shell_available() and not sb.python_available()
        with pytest.raises(osplat.Unsupported):
            sb.wrap(["echo"], "")
        with pytest.raises(osplat.Unsupported):
            sb.shell_profile(["/tmp"])
        assert sb.failed_to_start(65, "sandbox-exec: bad profile") is False


@pytest.mark.parametrize("name", NAMES)
def test_proc_group_kwargs_start_a_separate_group(name: str) -> None:
    kw = osplat._build(name).proc.new_group_kwargs()
    assert kw == ({"start_new_session": True} if name != "win32" else {"creationflags": kw.get("creationflags")})
    assert osplat._build(name).proc.signal_group(None, 9) is None  # never raises, never signals group 0


# ---- the current platform, for real ----
def test_current_is_chosen_once_from_sys_platform() -> None:
    assert osplat.current() is osplat.current()
    want = "darwin" if sys.platform == "darwin" else "win32" if sys.platform == "win32" else "linux"
    assert osplat.name() == want and osplat.capabilities() == osplat.current().capabilities()
    assert osplat.has("no-such-capability") is False


def test_use_swaps_and_restores_the_platform() -> None:
    before = osplat.current()
    with osplat.use("darwin"):
        assert osplat.name() == "darwin" and osplat.has("screenshot")
    assert osplat.current() is before


@pytest.mark.skipif(sys.platform == "win32", reason="posix process groups")
def test_current_proc_signals_and_kills_a_whole_group() -> None:
    proc_if = osplat.current().proc
    child = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30"], **proc_if.new_group_kwargs())
    try:
        time.sleep(0.2)
        pgid = proc_if.group_of(child.pid)
        assert pgid == child.pid
        proc_if.kill_tree(child.pid, child)
        assert child.wait(timeout=5) != 0
        time.sleep(0.1)
        assert proc_if.group_of(child.pid) is None
    finally:
        child.kill()


def test_an_opener_that_fails_surfaces_as_an_error_the_route_maps_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from personal_os import app as appmod

    def boom(path: str, reveal: bool = False) -> None:
        raise OSError("no opener")
    monkeypatch.setattr(appmod, "_desktop_open", boom)
    with pytest.raises(HTTPException) as e:
        appmod._run_open("/x")
    assert e.value.status_code == 400


def test_memory_probe_is_a_fraction_or_unknown() -> None:
    f = osplat.current().proc.memory_available_fraction()
    assert f is None or 0.0 <= f <= 1.0


# ---- linux pure parts ----
def test_linux_meminfo_parse() -> None:
    text = "MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:    4000000 kB\n"
    assert linux.parse_meminfo(text) == pytest.approx(0.25)
    assert linux.parse_meminfo("MemTotal: 10 kB\n") is None and linux.parse_meminfo("") is None
    assert linux.parse_meminfo("MemTotal: 10 kB\nMemAvailable: 99 kB\n") == 1.0


def test_linux_opens_through_xdg_open(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(linux, "run_opener", lambda argv: seen.append(argv))
    d = osplat._build("linux").desktop
    d.open_path("/tmp/a/b.txt")
    d.open_path("/tmp/a/b.txt", reveal=True)
    d.open_url("https://example.com")
    assert seen == [["xdg-open", "/tmp/a/b.txt"], ["xdg-open", "/tmp/a"], ["xdg-open", "https://example.com"]]


# ---- darwin pure parts ----
def test_darwin_paths_are_the_library_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/Users/u")
    p = osplat._build("darwin").paths
    assert p.data_dir() == Path("/Users/u/Library/Application Support/personal-os/data")
    assert p.logs_dir() == Path("/Users/u/Library/Logs/Grain")
    assert p.trash_dir() == Path("/Users/u/.Trash") and p.app_bundle() == "/Applications/Grain.app"
    assert "Library/Keychains" in p.keychain_dirs() and p.system_keychains() == ("/Library/Keychains",)
    assert p.browser_support_dir() == "Library/Application Support"


def test_darwin_opens_through_open(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(darwin, "run_opener", lambda argv: seen.append(argv))
    d = osplat._build("darwin").desktop
    d.open_path("/a b/c.txt")
    d.open_path("/a b/c.txt", reveal=True)
    assert seen == [["open", "/a b/c.txt"], ["open", "-R", "/a b/c.txt"]]
    assert d.file_search_argv("lease", ["/x", "/y"], False) == ["mdfind", "-onlyin", "/x", "-onlyin", "/y", "lease"]
    assert d.file_search_argv("a.pdf", ["/x"], True) == ["mdfind", "-onlyin", "/x", "-name", "a.pdf"]


def test_darwin_sandbox_profiles_are_text_that_denies_by_default(tmp_path: Path) -> None:
    sb = osplat._build("darwin").sandbox
    prof = sb.shell_profile([str(tmp_path)], network=False)
    assert prof.startswith("(version 1)\n(deny default)") and "(deny network*)" in prof and "(allow network*)" not in prof
    assert ".ssh" in prof and "Library/Keychains" in prof
    assert "(allow network*)" in sb.shell_profile([str(tmp_path)], network=True)
    assert 'remote ip "localhost:4000"' in sb.shell_profile([str(tmp_path)], proxy_port=4000)
    py = sb.python_profile(str(tmp_path), sys.executable)
    assert "(deny default)" in py and "network" in py
    assert sb.failed_to_start(65, "sandbox-exec: invalid profile") and not sb.failed_to_start(1, "sandbox-exec: x")
    assert not sb.failed_to_start(65, "something else")


def test_sandbox_module_keeps_its_old_names() -> None:
    from personal_os import sandbox
    assert sandbox.shell_profile(["/tmp"]) == osplat._build("darwin").sandbox.shell_profile(["/tmp"])
    assert callable(sandbox._mac_profile) and sandbox.MDNS_SOCKET


def test_vm_stat_parse_lives_with_the_darwin_probe() -> None:
    out = "Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: 100.\nPages inactive: 50.\nPages speculative: 50.\n"
    assert darwin.parse_vm_stat(out, 4096 * 1000) == pytest.approx(0.2)
    from personal_os import workers
    assert workers.parse_vm_stat is darwin.parse_vm_stat


# ---- windows pure parts ----
def test_windows_paths_and_group_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", "C:/Users/u/AppData/Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/u/AppData/Local")
    p = osplat._build("win32").paths
    assert p.data_dir() == Path("C:/Users/u/AppData/Roaming/personal-os/data")
    assert p.logs_dir() == Path("C:/Users/u/AppData/Local/Grain/Logs")
    assert osplat._build("win32").proc.new_group_kwargs()["creationflags"]


# ---- wiring: capabilities decide which tools are offered, and the route reports them ----
def test_capabilities_route_and_unavailable_tools() -> None:
    from fastapi.testclient import TestClient
    from personal_os.app import AUTH_TOKEN, app
    anon = TestClient(app)
    assert anon.get("/capabilities").status_code == 401
    c = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    body = c.get("/capabilities").json()
    assert body["platform"] == osplat.name() and set(body["capabilities"]) == CAPABILITY_KEYS
    tools = {t["name"]: t for t in c.get("/tools").json()["tools"]}
    for name, cap in (("screenshot", "screenshot"), ("find_files", "file_search"), ("list_shortcuts", "shortcuts"), ("run_shortcut", "shortcuts")):
        assert tools[name]["available"] is body["capabilities"][cap], name
    for name in ("read_local_file", "write_local_file", "trash_local_file"):
        assert tools[name]["available"], name  # plain file work needs no platform feature


def test_missing_capability_hides_the_mac_tools_and_present_one_shows_them() -> None:
    from personal_os.app import toolbox as tb
    with osplat.use("linux"):
        assert not tb.available("screenshot") and not tb.available("find_files") and not tb.available("run_shortcut")
    with osplat.use("darwin"):
        assert tb.available("screenshot")


def test_open_route_goes_through_the_desktop_object(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os import app as appmod
    seen: list[tuple[str, bool]] = []

    class Fake:
        def open_path(self, path: str, reveal: bool = False) -> None:
            seen.append((path, reveal))
    plat = osplat.current()
    fake = base.Platform(plat.name, plat.paths, plat.secrets, plat.sandbox, Fake(), plat.proc)  # type: ignore[arg-type]
    with osplat.use(fake):
        assert appmod._run_open("/x/y", reveal=True) == {"ok": True}
    assert seen == [("/x/y", True)]


# ---- nothing outside the platform layer reads the OS ----
OS_READS = re.compile(r"sys\.platform|platform\.system\(|os\.name\b|\bis_mac\(|\bIS_MAC\b|[\"']darwin[\"']|[\"']win32[\"']")
# macos.py is the AppKit/Quartz adapter: darwin-only by construction, and its tests flip IS_MAC.
ALLOWED = {"macos.py"}


def test_nothing_outside_the_platform_package_reads_the_os() -> None:
    root = BACKEND / "personal_os"
    hits: list[str] = []
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root)
        if rel.parts[0] == "osplat" or rel.name in ALLOWED:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if OS_READS.search(code):
                hits.append(f"{rel}:{n}: {line.strip()}")
    assert not hits, "ask osplat instead of reading the OS:\n" + "\n".join(hits)
