"""macOS Seatbelt (sandbox-exec) profile text. Pure string building: it runs on any platform so the profiles can be tested
without a Mac. The darwin Sandbox in darwin.py wraps argv with these; sandbox.py re-exports them under their old names."""
from __future__ import annotations

import os
import re

# Seatbelt matches the resolved path, and /var is a symlink to /private/var, so the literal has to be the real one.
MDNS_SOCKET = os.path.realpath("/var/run/mDNSResponder")


def _q(p: str) -> str:
    """A path as a quoted SBPL literal (the repo path contains a space)."""
    return '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _paths() -> tuple[str, str, str]:
    """(home, repo root, app data dir): the places a sandbox must never read from."""
    home = os.path.expanduser("~")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    try:
        from ..db import data_dir_from_env  # lazy: avoids an import cycle
        data = str(data_dir_from_env())
    except Exception:
        data = os.path.join(root, "data")
    return home, root, data


def _sbpl_re(p: str) -> str:
    """A literal path inside an SBPL regex."""
    return re.sub(r'([.^$*+?()\[\]{}|\\"])', r"\\\1", p)


# Files that run code or configure tools the next time the user, git, an editor or an agent opens the folder (or at login:
# launch agents and daemons). No shell write may touch them, a desk workspace included: the block goes last, so it beats every
# allow above it.
_PROTECTED_WRITES = r"""(deny file-write* (regex #"/\.(zshrc|zprofile|zshenv|zlogin|bashrc|bash_profile|profile|envrc|gitconfig|mcp\.json)$")
                  (regex #"/\.git/(hooks|config|info)(/|$)") (regex #"/\.vscode/(tasks|settings)\.json$")
                  (regex #"/\.husky(/|$)") (regex #"/\.auth_token$") (regex #"/personal-os\.db")
                  (regex #"/Library/(LaunchAgents|LaunchDaemons)(/|$)"))
"""


def _credential_denies(home: str) -> str:
    """The credential stores of mac.sensitive_reason as SBPL terms, denied for read AND write: a command that really needs one
    runs unsandboxed, which is a card the user answers. Key files by suffix (.pem, .key) are not here: system certificates
    use them. `home` is the real path of the home folder."""
    from .. import mac
    homes = list(dict.fromkeys([home, os.path.expanduser("~")]))
    subs = [f"(subpath {_q(os.path.join(h, d))})" for h in homes for d in mac.CRED_HOME_DIRS]
    subs += [f"(subpath {_q(d)})" for d in mac.SYSTEM_KEYCHAINS]
    lits = [f"(literal {_q(os.path.join(h, f))})" for h in homes for f in mac.CRED_HOME_FILES]
    support = "|".join(_sbpl_re(os.path.join(h, "Library", "Application Support")) for h in homes)
    browsers = "|".join(_sbpl_re(d) for d in mac.BROWSER_DIRS)
    names = "|".join(_sbpl_re(n) for n in mac.BROWSER_FILE_NAMES)
    rx = [rf'(regex #"^({support})/({browsers})/(.*/)?({names})$")', r'(regex #"/\.env($|\.)")', r'(regex #"/\.auth_token$")',
          r'(regex #"/personal-os\.db")', r'(regex #"/id_(rsa|dsa|ecdsa|ed25519)$")']
    return "\n".join(f"(deny file-read* file-write* {t})" for t in [*subs, *lits, *rx])


def shell_profile(writable: list[str], network: bool = False, proxy_port: int | None = None,
                  allow_hosts: list[str] | None = None, loopback: bool = False) -> str:
    """Seatbelt profile for the host shell (shell.py): blanket deny, then what a shell needs, then targeted denies.

    Unlike run_python's allowlist, a shell has to run whatever the user's toolchain is, so reads AND writes are open
    (`(allow file-write*)` everywhere) and the protected places are the denylist, in this order (the last match wins):
    (1) no write to Grain's own data folder, app bundles or code, and no read of the data folder or code; (2) the late
    re-allows: `writable` folders that sit inside the data folder (a desk workspace, the opencode state dir) and the work
    venv; (3) the credential stores, denied for read and write (`_credential_denies`); (4) `_PROTECTED_WRITES`, the files
    that run code later outside the sandbox (rc files, git hooks and config, editor tasks, launch agents, the auth token,
    the database). Network is off unless `network`; with `proxy_port` (and not `network`) the one thing it may connect to
    is the allowlisting proxy on localhost at that port (egress.py).
    """
    from .. import mac
    home, root, data = _paths()
    net = "(allow network*)" if network else "(deny network*)"
    if proxy_port and not network:
        net += f'\n(allow network-outbound (remote ip "localhost:{int(proxy_port)}"))'
    remote = False
    for h in allow_hosts or []:  # "localhost:4000" or "*:443": Seatbelt matches ports and localhost, never other names
        if not network and re.fullmatch(r"(localhost|\*):\d{1,5}", h):
            net += f'\n(allow network-outbound (remote tcp "{h}"))'
            remote = remote or h.startswith("*:")
    if remote:
        # getaddrinfo talks to mDNSResponder over its unix socket, which `(deny network*)` also covers: without this
        # every hostname fails ENOTFOUND and the tcp allow above can only be used with a literal IP. No new reach -
        # `*:port` already permits any host; open network (`allow network*`) covers the socket by itself.
        net += f'\n(allow network-outbound (remote unix-socket (path-literal {_q(MDNS_SOCKET)})))'
    if loopback and not network:  # a program that talks to itself over a local port (opencode's private server)
        net += '\n(allow network-bind network-inbound (local ip "localhost:*"))\n(allow network-outbound (remote ip "localhost:*"))'
    # The shared work venv lives under the app data dir, which is denied above; the shell has its bin first on PATH, so it must
    # be able to read it (pip, python). Appended after the deny so it wins; only that folder, never the database beside it.
    late = ""
    try:
        from .. import envs
        wb = envs.work_bin()
    except Exception:  # noqa: BLE001
        wb = None
    if wb:
        late = f"(allow file-read* (subpath {_q(os.path.realpath(os.path.dirname(wb)))}))\n"
    data_real, home_real = os.path.realpath(data), os.path.realpath(home)
    # The data folder as configured and as resolved, and the app bundles: never written. The folder is never read either.
    kept = [str(x) for x in mac.protected_paths() if x.suffix.lower() != ".app"] + [data, data_real]
    data_subs = " ".join(f"(subpath {_q(d)})" for d in dict.fromkeys(kept))
    bundles = " ".join(f"(subpath {_q(str(b))})" for b in dict.fromkeys(x for x in mac.protected_paths() if x.suffix.lower() == ".app"))
    # A desk workspace lives inside the app data dir, which the deny above covers. Re-allow only those writable folders; the
    # credential block after them repeats the secret-name denies, so a database or .env there still loses.
    inside = list(dict.fromkeys(rp for p in writable if (rp := os.path.realpath(p)).startswith(data_real + os.sep)))
    if inside:
        late += f"(allow file-read* file-write* {' '.join(f'(subpath {_q(p)})' for p in inside)})\n"
    return f"""(version 1)
(deny default)
{net}
(allow sysctl-read)
(allow process-fork)
(allow process-exec)
(allow process-info*)
(allow signal (target same-sandbox))
(allow file-read*)
(allow file-write*)
(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/tty") (literal "/dev/dtracehelper"))
(allow mach-lookup)
(allow ipc-posix-shm)
(allow pseudo-tty)
(deny appleevent-send)
(deny file-write* {data_subs} {bundles} (literal {_q(os.path.join(root, ".env"))})
                  (subpath {_q(os.path.join(root, "backend", "personal_os"))}))
(deny file-read* {data_subs} (literal {_q(os.path.join(root, ".env"))})
                 (subpath {_q(os.path.join(root, "backend", "personal_os"))}))
{late}{_credential_denies(home_real)}
{_PROTECTED_WRITES}"""


def python_profile(work: str, py: str, socket_path: str | None = None, workspace: str | None = None) -> str:
    """Least-privilege sandbox-exec profile for one run_python call.

    Threat model: the script is model-written, the model's context routinely holds
    untrusted third-party text, and run_python stdout flows straight back into that
    context. Network is denied, so *reads are the exfiltration channel*: anything the
    script can open can be printed. Reads are therefore an allowlist (interpreter,
    site-packages, the work dir, system libs/fonts/tzdata) with
    the app's own secrets (.env, personal-os.db*, .auth_token), ssh/aws/gpg keys and
    browser profiles denied outright. process-exec and mach-lookup are allowlisted too,
    so the "executes" tier cannot shell out to osascript and silently become "external".
    Writes are confined to the run's own temp work dir, so no state survives to the next run - except in a cowork desk,
    where `workspace` (the desk's own folder, symlink-resolved) is readable and writable too: that is what the desk is
    for. The app data dir is denied below, and the workspace and the shared work venv both live inside it, so their
    allows come after that deny; the secret-name denies are repeated after them and still win.

    SBPL evaluates every rule and the last match wins: blanket deny, then the allows,
    then the targeted denies.
    """
    exe = os.path.realpath(py)
    base = os.path.dirname(os.path.dirname(exe))                    # interpreter + stdlib + lib-dynload
    venv = os.path.realpath(os.path.dirname(os.path.dirname(py)))   # site-packages: numpy, matplotlib, PIL/.dylibs
    work = os.path.realpath(work)
    home, root, data = _paths()
    # The tool bridge's Unix socket (toolbridge.py) is the one network the script gets: connect to that path, nothing else.
    names = r'(regex #"/\.env$") (regex #"/\.auth_token$") (regex #"/personal-os\.db")'
    late = ""
    if workspace:
        wsp = os.path.realpath(workspace)
        late = f"(allow file-read* file-write* (subpath {_q(wsp)}))\n"
    if os.path.realpath(venv).startswith(os.path.realpath(data) + os.sep):  # the work env under <data>/envs
        late += f"(allow file-read* (subpath {_q(os.path.realpath(venv))}))\n"
    sock = (f'(allow network-outbound (remote unix-socket (path-literal {_q(os.path.realpath(socket_path))})))\n'
            f'(allow file-read* file-write* (literal {_q(os.path.realpath(socket_path))}))\n') if socket_path else ""
    return f"""(version 1)
(deny default)
(deny network*)
{sock}(allow sysctl-read)
(allow process-fork)
(allow signal (target self))
(allow file-read-metadata)
(allow ipc-posix-shm-read* (ipc-posix-name "apple.shm.notification_center"))
(allow process-exec (literal {_q(py)}) (literal {_q(exe)}) (subpath {_q(base)}))
(allow file-read* (subpath {_q(base)}) (subpath {_q(venv)}) (subpath {_q(work)})
                  (subpath "/usr/lib") (subpath "/usr/share") (subpath "/System/Library")
                  (subpath "/Library/Fonts") (subpath "/private/var/db/timezone")
                  (literal "/private/etc/localtime") (literal "/private/etc/mime.types") (literal "/private/etc/apache2/mime.types")  ; openpyxl & co. build a MimeTypes() at import
                  (literal "/dev/null") (literal "/dev/zero")
                  (literal "/dev/random") (literal "/dev/urandom") (subpath "/dev/fd")
                  (literal "/"))                    ; dyld stats the root dir; without it python aborts at startup
(allow file-write* (subpath {_q(work)}))
(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.opendirectoryd.membership")
  (global-name "com.apple.system.notification_center")
  (global-name "com.apple.system.logger")
  (global-name "com.apple.logd")
  (global-name "com.apple.logd.events")
  (global-name "com.apple.diagnosticd")
  (global-name "com.apple.bsd.dirhelper"))
(deny appleevent-send)
(deny file-read* (literal {_q(os.path.join(root, ".env"))}) (subpath {_q(data)})
                 (subpath {_q(os.path.join(root, "backend", "personal_os"))})
                 (subpath {_q(os.path.join(home, ".ssh"))}) (subpath {_q(os.path.join(home, ".aws"))})
                 (subpath {_q(os.path.join(home, ".config"))}) (subpath {_q(os.path.join(home, ".gnupg"))})
                 (subpath {_q(os.path.join(home, ".kube"))})
                 (subpath {_q(os.path.join(home, "Library", "Keychains"))})
                 (subpath {_q(os.path.join(home, "Library", "Application Support", "Google", "Chrome"))})
                 (subpath {_q(os.path.join(home, "Library", "Application Support", "BraveSoftware"))})
                 (subpath {_q(os.path.join(home, "Library", "Application Support", "Firefox"))})
                 (subpath {_q(os.path.join(home, "Library", "Safari"))})
                 (regex #"/\\.env$") (regex #"/\\.auth_token$") (regex #"/personal-os\\.db"))
{late}(deny file-read* {names})
"""
