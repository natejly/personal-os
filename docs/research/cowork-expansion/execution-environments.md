# R5: Agent execution environments and sandboxes (what Grain should add)

Method: sources fetched 2026-10-02 (sandbox-runtime README, Claude Code sandboxing docs, Codex comparison article, Apple container coverage, OpenHands/E2B docs). Items marked (unverified) come from search snippets or general knowledge, not a primary source. Repo only grepped (backend/personal_os/shell.py, sandbox.py, microvm.py exist); not modified.

## 1. Network egress control

Sources: https://github.com/anthropic-experimental/sandbox-runtime ; https://code.claude.com/docs/en/sandboxing

Design (sandbox-runtime / Claude Code):
- macOS: `sandbox-exec` with a generated Seatbelt profile. Outbound network denied except `localhost:<proxy ports>`. Two proxies run OUTSIDE the sandbox: HTTP/HTTPS CONNECT proxy and SOCKS5 proxy (ssh/db/other TCP). Env in the sandbox: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` (+ lowercase, NO_PROXY). `curl --noproxy '*'` fails ("could not resolve host") since there is no route.
- Linux: socat relays over unix sockets into the proxy (not needed on macOS).
- Config: `network.allowedDomains` (wildcards `*.npmjs.org`, optional `:port`), `deniedDomains` (checked first, wins), empty allowlist = no network. Claude Code starts empty and prompts per new host; `strictAllowlist` / managed `allowManagedDomainsOnly` = deny without prompt. `WebFetch(domain:...)` allow rules fold into the allowlist.
- Post-allow DNS check: after the hostname passes, the proxy resolves it and refuses names resolving only to loopback / link-local / cloud metadata (169.254.169.254). Stops rebinding/SSRF to local services. RFC1918 allowed unless configured.
- Matching is hostname only, no TLS inspection by default; known gap = domain fronting. Optional experimental `network.tlsTerminate`.
- Credential masking: sandbox sees a per-session sentinel for GH_TOKEN etc.; proxy substitutes the real value only on connections to `injectHosts` that are also allowlisted (needs TLS termination). Env vars can be `deny`ed. Docker sbx has the same "credential proxy" idea.
- Escape hatch: `dangerouslyDisableSandbox` retry goes through the normal permission prompt; `allowUnsandboxedCommands:false` disables it. `excludedCommands` patterns (Bash(...) syntax) run unsandboxed; docs warn interpreters/scripts can launder access.
- Sandboxed commands auto-approved (`autoAllowBashIfSandboxed` default true). Denied host is named in the tool result so the model can retry/declare it.
- Always write-protected even inside allowed roots: shell rc files, git hooks/config, IDE dirs. Globs on macOS.

Codex CLI: modes read-only / workspace-write / danger-full-access; `[sandbox_workspace_write] network_access`, `writable_roots`. Seatbelt network is binary, no domain filtering (https://codex.danielvaughan.com/2026/04/24/agent-sandbox-comparison-codex-seatbelt-openshell-docker-sbx/). Docker sbx: presets Open / Balanced (AI services + package managers) / Locked Down. NVIDIA OpenShell: per-binary + per-host + per-method rules, hot reload.

Safe package installs: allow only registries (pypi.org, files.pythonhosted.org, registry.npmjs.org, optionally github release hosts) through the proxy; pip/uv/npm honor HTTP(S)_PROXY, so no code changes. Residual risk: install scripts are arbitrary code, but run inside the same sandbox (writes confined).

Seatbelt recipe for Grain:
```
(deny network*)
(allow network-outbound (remote ip "localhost:PORT"))
; no broad unix-socket connect (mDNSResponder, docker.sock are bypass paths)
```
Proxy = small asyncio server (in the FastAPI process or sidecar), bound 127.0.0.1 on an ephemeral port per desk. Handles `CONNECT host:port` (check host+port vs allow/deny, resolve, reject loopback/link-local/metadata, then pipe bytes) and plain-HTTP absolute-URI requests. Log every decision to the run tape. Unknown host -> approval (reuse existing approval machinery); result string tells the model "blocked host X".

## 2. Stateful execution
- Claude Code Bash: cwd persists across calls, env does not. Monitor tool streams background process output as events (also sandboxed).
- OpenHands (https://docs.openhands.dev/sdk/guides/agent-interactive-terminal): tmux-backed persistent bash; params `command`, `is_input` (send text/C-c/C-d to the running process), `timeout`, `no_change_timeout_seconds` (~30 s default): returns when output is idle with status `NO_CHANGE_TIMEOUT` and a suffix telling the model to wait, send input, or kill. Hard timeout returns partial output and leaves process running. Separate persistent IPython kernel tool.
- E2B code-interpreter: `run_code(code, context=...)`; kernel state persists per context. Execution object: `results[]` with multi-format reprs (text, html, png, jpeg, svg, json, latex), `logs.stdout/stderr`, `error{name,value,traceback}`; pre-installed data libs; pip install works.
- Anthropic code-execution tool / OpenAI code interpreter (unverified details): container persists files per session; plots/files returned as file ids.
- E2B pause/resume (https://docs.e2b.dev/docs/sandbox/persistence): pauses fs + memory (processes, variables); ~4 s per GiB RAM to pause, ~1 s to resume; states running/paused/snapshotting/killed; auto-pause on timeout.
- Output to model/UI: images as base64 blocks (downscaled) for the model AND files in the workspace for UI; DataFrames as text (shape, dtypes, head) for the model, HTML repr for UI; large output as paged handle (Grain has this).

## 3. Isolation tech on macOS (2026)
- Seatbelt/sandbox-exec: nominally deprecated but what Claude Code, Codex, Cursor use on macOS; negligible startup, native speed. Weak: SBPL undocumented, cannot cap memory/CPU, unix sockets/mach services need care.
- Apple `container` + Containerization (macOS 26, Apple silicon, 1.0 in 2026): one lightweight Linux VM per container via Virtualization.framework, Swift init `vminitd`, sub-second boot (~100 ms claimed), virtiofs sharing. Secondary sources only: https://thenewstack.io/apple-containers-on-macos-a-technical-comparison-with-docker/. CPU/memory flags (unverified). Needs macOS 26.
- colima/lima: one shared Linux VM (vz + virtiofs); VM start seconds, containers ms after. Current Grain `sandbox_*` path. Weaker isolation between containers than VM-per-container.
- Docker sbx: microVM per sandbox with own kernel/daemon, credential proxy; seconds to start, heavy for small tasks.
- gVisor/Firecracker: Linux-only; irrelevant locally.
- Takeaway: Seatbelt fast path; VM container for Linux/root/apt/untrusted binaries/long servers. Layering is normal.

## 4. Hosted sandboxes: API lessons
- E2B: `Sandbox.create(template, timeout)`, `files.read/write`, `commands.run(background=True)` returns a handle, `get_host(port)` preview URL, pause/resume/snapshot, Dockerfile templates.
- Daytona/Modal/Vercel/Cloudflare (unverified): image/fs snapshots for fast cold start, signed preview URLs, volumes separate from ephemeral fs, idle TTL.
- Lessons: template = prebuilt base per desk type; background handle + log cursor; explicit TTL/auto-pause; preview URL is a tool return value, not scraped from logs.

## 5. Dev-server and preview
- Claude Code: background Bash + Monitor streaming new lines; browser pane preview. Pattern: start in background, wait for readiness (port connect or log regex like "Local: http://"), tail log, open pane.
- Port detection: regex `https?://(localhost|127.0.0.1):(\d+)` on stdout, or `lsof -iTCP -sTCP:LISTEN -a -p <pids of the process group>`. Readiness: TCP connect then HTTP 200 poll with backoff, bounded 30-60 s, abort if process exits.
- Grain has a `<webview>` widget with frozen-src; add a preview tool that opens it at the detected 127.0.0.1 URL. Seatbelt: allow network-bind/inbound only on localhost.

## 6. Environment bootstrap
- uv: `uv venv` per desk (e.g. `<workspace>/.grain/venv`), shared UV_CACHE_DIR (hardlinks make reinstalls near instant; `UV_LINK_MODE=copy` if cache is outside allowed-write), `uv pip install --offline` from cache.
- Detection: pyproject/requirements/uv.lock, package.json + lockfile, Cargo.toml, go.mod; inject one-line "environment facts" (python path, node version, which of pandoc/soffice/ffmpeg exist).
- Knowledge-work base: pandas, numpy, scipy, matplotlib (Agg), seaborn, openpyxl, xlsxwriter, python-docx, python-pptx, pypdf, pdfplumber, reportlab, pillow, bs4, lxml, httpx, jinja2, ipykernel. System: pandoc, LibreOffice headless (`soffice --headless --convert-to pdf`), ffmpeg, poppler, tesseract, node. LibreOffice is ~700 MB: detect-and-offer or keep in the docker image, not the DMG.
- Env hygiene: `PIP_REQUIRE_VIRTUALENV`, `PYTHONDONTWRITEBYTECODE`, `MPLBACKEND=Agg`, per-desk HOME/TMPDIR so tool caches stay in allowed paths.

## 7. Resource and safety limits
- Timeouts: two-tier (soft no-output returns partial and leaves running; hard kills process group). 30 s no-change; ~120 s default / 600 s max foreground (Claude Code Bash).
- Output: head+tail truncation (~30k chars), full log spilled to file with handle.
- Process groups: `start_new_session=True`, `os.killpg` TERM then KILL after grace; psutil descendant tracking (setsid daemons escape pgid); reap on desk end/app quit.
- CPU/mem/disk: Seatbelt can't enforce. `setrlimit` RLIMIT_CPU/NOFILE/FSIZE work on macOS; RLIMIT_AS/RSS largely not on Darwin, so use an RSS watchdog (psutil). Disk: watchdog on workspace size growth (no APFS quotas). Hard caps need a VM (`--memory`, `--cpus`).
- Secrets: redact outputs against actual secret values + regexes (sk-, AKIA, ghp_, JWT, PEM) before they reach the transcript; env deny list; sentinel + proxy injection for legit credentials.
- Protected paths always read-only inside the workspace: .git/hooks, .git/config, shell rc files, LaunchAgents.

## Mechanisms worth copying
1. Allowlist proxy outside sandbox + Seatbelt localhost-only egress; proxy env vars; post-resolve IP check; deny beats allow; empty default; prompt-on-new-host with denied host named in tool result.
2. `no_change_timeout` and `is_input` semantics for shell_poll; `shell_send(id, text, enter, ctrl)`.
3. Persistent Python kernel per desk with Execution-like result {stdout, stderr, error, results:[{mime,data}], files_created}.
4. Sentinel credential injection.
5. Auto-approve inside-sandbox commands; only escalations (unsandboxed, new host, outside-root write) prompt.
6. Background handle + log cursor + readiness probe + preview URL.
7. Pause/resume of desk environment (fs already via shadow-git; record processes/kernel cell log).

## Not worth copying / risks
- TLS termination / local CA: fragile (pinning, certifi, node) and a liability. Hostname allowlist suffices v1.
- SOCKS proxy: skip v1; HTTP(S) covers pip/npm/git-https/curl.
- tmux dependency: not on macOS by default; Python pty/ptyprocess + ring buffer gives the same semantics.
- Hosted sandboxes: conflict with local-first.
- Apple `container` as primary runtime: macOS 26 + Apple silicon only, young; keep colima, optional later backend.
- Broad unsandboxed-escape patterns: keep Grain's unsandboxed runs always-ask.
- Allowlists do not stop exfiltration to allowed hosts (gists, wildcard hosts): keep wildcards narrow; taint-gate runs that read mail/secrets before network.
- Do not rely on RLIMIT_AS on macOS.
- Seatbelt deprecation: works today, no lightweight replacement; keep a VM fallback.

## Ranked recommendations for Grain

1. **Domain-allowlist egress proxy for shell_run/run_python (replace the global network switch).** Build: in-process asyncio CONNECT proxy, per-desk allowlist (default "Packages" preset: pypi.org, files.pythonhosted.org, registry.npmjs.org, github release hosts), Seatbelt profile allows only that localhost port, set HTTP(S)_PROXY/ALL_PROXY/NO_PROXY, new host -> existing approval flow, decisions on run tape, presets Open/Packages/Locked. Why: unlocks installs and data fetches without all-or-nothing. Effort M. Failure modes: tools ignoring proxy vars fail closed (document); DNS rebinding/loopback/metadata (post-resolve IP check); broad unix-socket allow bypasses proxy; domain fronting on wildcards; per-desk port isolation; unattended runs must only propose allowlist growth.

2. **Persistent shell session per desk with idle-timeout polling and stdin.** Build: PTY-backed long-lived zsh launched under sandbox-exec, cwd + env carried across calls, `shell_send` (text, control keys), `no_change_timeout` on shell_poll, ring buffer + spill file, sentinel marker per command. Effort M. Failure modes: prompt/echo noise; zombie shells after quit (supervisor kill by pgid); interactive prompts hanging (soft timeout + hint); env secrets leaking across commands (re-scrub); `Bash(pattern)` rules must still match each submitted command.

3. **Persistent Python kernel tool (`py_exec`) with rich results.** Build: per-desk ipykernel (jupyter_client) under Seatbelt in the desk venv; results {stdout, stderr, error, results:[{mime,data}]}; images saved to `<workspace>/.outputs/` plus downscaled image to the model; DataFrame summary; reuse tool bridge; idle shutdown, restart, per-cell timeout with interrupt. Effort M-L. Failure modes: crash/OOM (RSS watchdog, auto-restart, tell model state is lost); hidden state (log cells to a replayable notebook in the workspace); output floods (cap + handle); ZMQ needs localhost ports in the profile (or ipc transport in the temp dir).

4. **Per-desk uv venv + base knowledge-work env + environment-facts block.** Build: lazy `uv venv`, shared UV_CACHE_DIR, preinstalled data/doc stack, probe pandoc/soffice/ffmpeg/node and inject a one-liner, `env_install` tool = `uv pip install` via proxy, approval for non-curated packages. Effort M. Failure modes: typosquats (ask on first-time packages, pin, write requirements.lock into workspace); disk bloat (cache GC); interpreter drift between shell and kernel (same VIRTUAL_ENV on PATH); wheel gaps on Apple silicon.

5. **Background process service: handles, log cursor, readiness, port detection, preview.** Build: `shell_run(background=true, ready_when={port|log_regex|url}, ready_timeout)`, auto-detected ports (lsof on process group), `process_logs(id, since)`, Monitor-like push of matching new lines to the desk notify channel, `open_preview(url)` into the existing webview widget; Seatbelt bind/inbound on localhost only. Effort M. Failure modes: port collisions (pass PORT, pick free port); orphaned servers (registry + startup reaper); preview restricted to 127.0.0.1 and the desk's own pids; log floods (rate limit/dedupe); false-positive readiness.

6. **Hardened process control + watchdog limits.** Build: `start_new_session`, killpg TERM->KILL, psutil descendants, per-call wall/no-output limits, RSS watchdog (default ~4 GiB), RLIMIT_FSIZE/NOFILE, workspace growth cap, head+tail output with spill file, redaction pass on every tool result. Effort S-M. Failure modes: legit big builds killed (per-desk configurable, message names the limit); redaction false positives (redact in transcript only, never in files); setsid escapers (tree tracking).

7. **Credential use without exposure.** Because HTTPS CONNECT without TLS termination cannot inject headers, build a host-side authenticated fetch via the tool bridge (`grain-fetch` helper callable from scripts) rather than a MITM proxy. Effort M. Failure modes: real tokens in logs (redaction), over-broad host scope, replay to other hosts.

8. **Container tier for Linux-only/heavy work.** Build: `shell_run(runtime="linux")` routed to colima with workspace bind mount (virtiofs), `--memory/--cpus`, HTTP_PROXY=host proxy, prebuilt "grain-work" image (pandoc, LibreOffice, ffmpeg, node, python data stack). Effort L. Failure modes: virtiofs permissions/slow metadata (node_modules in a named volume); colima not running (health check/start); image pull/size (build once, cache); never mount docker.sock. Apple `container` as a later backend once macOS 26 is the floor.

9. **Desk environment checkpoint/resume.** Build: on pause persist {cwd, env diff, venv lock, process start commands, kernel cell log}; on resume restart declared servers and optionally replay pure cells. Effort M. Failure modes: replay side effects (opt-in only), non-determinism, replay time.

10. **`convert_document` tool with render-to-image QA.** Build: sandboxed wrappers over pandoc, `soffice --headless --convert-to`, pdftoppm so the model can view PNGs of generated docx/pptx. Effort S-M. Failure modes: soffice profile lock / HOME writes (`-env:UserInstallation=file://<tmp>`, kill leftovers); hangs (hard timeout); missing fonts make layout checks mislead.

Suggested order: 6 -> 1 -> 4 -> 2 -> 3 -> 5 -> 10 -> 8 -> 7 -> 9.
