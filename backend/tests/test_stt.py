"""Speech to text: backend resolution, the proxy round trip, the capability rows, the self-test.

Nothing here touches the network. There is no /v1/audio/transcriptions route on this machine to
touch - that absence is the reason stt.py exists - so the proxy is driven through a stub client
and the only thing asserted about real audio is that a silent wav survives the trip.

Runs under pytest, or directly: python backend/tests/test_stt.py
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, llm, stt  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}
CFG = {"sttBackend": "auto", "sttModel": "whisper-1", "whisperModelPath": ""}

VERBOSE_JSON = {
    "task": "transcribe", "language": "en", "duration": 4.25,
    "text": " So the pricing page ships Thursday.",
    "segments": [{"id": 0, "start": 0.0, "end": 4.25, "text": " So the pricing page ships Thursday."}],
}


class _Reply:
    def __init__(self, status: int, payload: object, text: str):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _Client:
    def __init__(self, rec: proxy_replies):
        self.rec = rec

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def post(self, url: str, headers: dict | None = None, files: dict | None = None,
             data: dict | None = None) -> _Reply:
        name, fh, mime = (files or {})["file"]
        self.rec.calls.append({"url": url, "headers": headers or {}, "data": dict(data or {}),
                               "filename": name, "mime": mime, "bytes": fh.read()})
        if self.rec.raises is not None:
            raise self.rec.raises
        return _Reply(self.rec.status, self.rec.payload, self.rec.text)


class proxy_replies:
    """Answer the transcription POST without a network, and keep what was sent.

    `httpx.Client` is constructed inside `stt._proxy`, so the only seam is the class itself; it
    is restored on exit because the same module object is shared with llm.py and google.py.
    """

    def __init__(self, status: int = 200, payload: object = None, text: str = "",
                 raises: Exception | None = None):
        self.status = status
        self.payload = payload
        self.text = text
        self.raises = raises
        self.calls: list[dict] = []

    def __enter__(self) -> proxy_replies:
        self.real = stt.httpx.Client
        stt.httpx.Client = lambda *a, **kw: _Client(self)  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        stt.httpx.Client = self.real  # type: ignore[assignment]


class speech_is:
    """Pin whether Apple Speech looks ready, independent of this Mac's TCC grant."""

    def __init__(self, available: bool, authorized: bool | None = None):
        self.available = available
        self.authorized = available if authorized is None else authorized

    def __enter__(self) -> None:
        self.real_av = stt.speech_available
        self.real_au = stt.speech_authorized
        stt.speech_available = lambda: self.available  # type: ignore[assignment]
        stt.speech_authorized = lambda: self.authorized  # type: ignore[assignment]

    def __exit__(self, *exc: object) -> None:
        stt.speech_available = self.real_av  # type: ignore[assignment]
        stt.speech_authorized = self.real_au  # type: ignore[assignment]


class whisper_is:
    """Pin whether whisper.cpp looks installed.

    `shutil.which` otherwise reports whatever happens to be on this machine's PATH, and what
    `auto` resolves to is precisely the thing under test.
    """

    def __init__(self, cli: str = ""):
        self.cli = cli

    def __enter__(self) -> None:
        self.real = stt.whisper_cli_path
        stt.whisper_cli_path = lambda: self.cli  # type: ignore[assignment]

    def __exit__(self, *exc: object) -> None:
        stt.whisper_cli_path = self.real  # type: ignore[assignment]


def _wav(data: bytes = b"RIFF....WAVEfmt ") -> Path:
    """A file for the multipart body. The proxy path never decodes it, so bytes are enough."""
    path = Path(tempfile.mkdtemp()) / "mic-00003.wav"
    path.write_bytes(data)
    return path


def _model(data_dir: Path, name: str = "ggml-base.en.bin") -> Path:
    path = data_dir / "models" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ggml")
    return path


# ---------------------------------------------------------------- the proxy backend


def test_proxy_verbose_json_yields_text_and_segments() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with proxy_replies(200, VERBOSE_JSON, "") as stub, whisper_is(""), speech_is(False):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert res["backend"] == "proxy"
    assert res["error"] == ""
    assert res["text"] == " So the pricing page ships Thursday."
    assert len(res["detail"]["segments"]) == 1
    assert res["detail"]["segments"][0]["end"] == 4.25
    assert res["ms"] >= 0
    assert len(stub.calls) == 1
    assert stub.calls[0]["url"] == "http://localhost:4000/v1/audio/transcriptions"


def test_proxy_sends_the_model_verbose_json_and_the_previous_tail() -> None:
    data_dir = Path(tempfile.mkdtemp())
    tail = "x" * 3000 + "and then he said"
    with proxy_replies(200, VERBOSE_JSON, "") as stub, whisper_is(""), speech_is(False):
        stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir, prompt=tail)
        sent = stub.calls[0]
        assert sent["data"]["model"] == "whisper-1"
        assert sent["data"]["response_format"] == "verbose_json"
        # Capped, and capped from the END: the words next to the boundary are the useful ones.
        assert len(sent["data"]["prompt"]) == stt.MAX_PROMPT_CHARS
        assert sent["data"]["prompt"].endswith("and then he said")
        assert sent["filename"] == "mic-00003.wav" and sent["mime"] == "audio/wav"
        assert sent["bytes"] == b"RIFF....WAVEfmt "   # the handle was open when it was read
        assert sent["headers"] == {}                  # no apiKey set, so no Authorization header

        stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
        assert "prompt" not in stub.calls[1]["data"]  # the first segment has no previous tail

        stt.transcribe(_wav(), settings={**SETTINGS, "apiKey": "sk-local"}, cfg=CFG, data_dir=data_dir)
        assert stub.calls[2]["headers"] == {"Authorization": "Bearer sk-local"}


def test_proxy_bills_the_audio_length_not_the_wall_clock() -> None:
    # AudioCollector._transcribe builds its own httpx client, so _emit_usage never fires for it
    # and transcription minutes are invisible in GET /usage. This is the one caller of
    # llm.audio_usage; nothing downstream should emit a second row for the same segment.
    data_dir = Path(tempfile.mkdtemp())
    seen: list[dict] = []
    listener = seen.append
    llm.on_usage(listener)
    try:
        with proxy_replies(200, VERBOSE_JSON, ""), whisper_is(""), speech_is(False):
            stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
        assert len(seen) == 1, seen
        assert seen[0]["model"] == "whisper-1"
        assert seen[0]["kind"] == "meeting-stt"
        assert seen[0]["duration_ms"] == 4250           # VERBOSE_JSON's duration, in ms
        # No duration in the reply means no row: a made-up number is worse than a missing one.
        with proxy_replies(200, {"text": "hi"}, ""), whisper_is(""), speech_is(False):
            stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
        assert len(seen) == 1, seen
    finally:
        # on_usage has no matching off_usage; leaving the listener attached would leak into
        # whatever test file pytest loads next in the same process.
        with contextlib.suppress(ValueError):
            llm._usage_listeners.remove(listener)


def test_proxy_404_returns_an_error_and_never_raises() -> None:
    # The state of this machine today: litellm.yaml routes chat models and nothing else.
    data_dir = Path(tempfile.mkdtemp())
    body = '{"error":{"message":"The model `whisper-1` does not exist","code":404}}'
    with proxy_replies(404, None, body), whisper_is(""), speech_is(False):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert res["error"], res
    assert res["error"].startswith("transcription 404: ")
    assert res["text"] == ""
    assert res["detail"] == {}
    assert res["backend"] == "proxy"


def test_proxy_reply_that_is_not_json_falls_back_to_raw_text() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with proxy_replies(200, None, "plain text transcript"), whisper_is(""), speech_is(False):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert res["text"] == "plain text transcript"
    assert res["error"] == ""
    # A JSON array is valid JSON and still not a transcription payload.
    with proxy_replies(200, [1, 2, 3], "[1, 2, 3]"), whisper_is(""), speech_is(False):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert res["text"] == "[1, 2, 3]"
    assert res["error"] == ""


def test_a_dead_connection_comes_back_as_an_error_string() -> None:
    # TranscribeWorker calls this on a daemon thread; an exception escaping would kill the queue.
    data_dir = Path(tempfile.mkdtemp())
    with proxy_replies(raises=stt.httpx.ConnectError("All connection attempts failed")), whisper_is(""), speech_is(False):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert res["error"] == "ConnectError: All connection attempts failed"
    assert res["text"] == "" and res["backend"] == "proxy"


# ---------------------------------------------------------------- resolution and the other backends


def test_resolve_backend_prefers_proxy_when_whisper_is_not_installed() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with whisper_is(""), speech_is(False):
        assert stt.resolve_backend(CFG, data_dir) == "proxy"
        _model(data_dir)                                  # a model with no binary is still proxy
        assert stt.resolve_backend(CFG, data_dir) == "proxy"
    with whisper_is("/opt/homebrew/bin/whisper-cli"), speech_is(False):
        assert stt.resolve_backend(CFG, data_dir) == "local"
        assert stt.resolve_backend(CFG, Path(tempfile.mkdtemp())) == "proxy"   # binary, no model


def test_resolve_backend_prefers_speech_when_it_is_authorized() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with speech_is(True), whisper_is("/opt/homebrew/bin/whisper-cli"):
        _model(data_dir)
        assert stt.resolve_backend(CFG, data_dir) == "speech"
    with speech_is(True, authorized=False), whisper_is(""):
        assert stt.resolve_backend(CFG, data_dir) == "proxy"


def test_resolve_backend_honours_an_explicit_setting() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with whisper_is(""), speech_is(False):
        assert stt.resolve_backend({"sttBackend": "local"}, data_dir) == "local"
        assert stt.resolve_backend({"sttBackend": "off"}, data_dir) == "off"
        assert stt.resolve_backend({"sttBackend": "PROXY"}, data_dir) == "proxy"
        assert stt.resolve_backend({}, data_dir) == "proxy"            # missing key means auto
        assert stt.resolve_backend({"sttBackend": "nonsense"}, data_dir) == "proxy"
        assert stt.resolve_backend({"sttBackend": "speech"}, data_dir) == "speech"
    assert "auto" not in {stt.resolve_backend({"sttBackend": b}, data_dir) for b in stt.BACKENDS}


def test_local_model_path_prefers_the_configured_file() -> None:
    data_dir = Path(tempfile.mkdtemp())
    assert stt.local_model_path(data_dir, CFG) == ""                   # no models dir at all
    auto = _model(data_dir, "ggml-small.bin")
    assert stt.local_model_path(data_dir, CFG) == str(auto)
    picked = _model(Path(tempfile.mkdtemp()), "ggml-large-v3.bin")   # kept outside data_dir
    assert stt.local_model_path(data_dir, {"whisperModelPath": str(picked)}) == str(picked)
    # A path the user set and then moved falls back to the directory rather than failing.
    assert stt.local_model_path(data_dir, {"whisperModelPath": "/nope/ggml.bin"}) == str(auto)


def test_local_backend_without_a_binary_reports_the_fix() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with whisper_is(""):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg={"sttBackend": "local"}, data_dir=data_dir)
    assert res["backend"] == "local"
    assert "brew install whisper-cpp" in res["error"]
    assert res["text"] == ""
    with whisper_is("/opt/homebrew/bin/whisper-cli"):
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg={"sttBackend": "local"}, data_dir=data_dir)
    assert "no whisper model found" in res["error"]


def test_off_explains_both_fixes_and_posts_nothing() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with proxy_replies(200, VERBOSE_JSON, "") as stub:
        res = stt.transcribe(_wav(), settings=SETTINGS, cfg={"sttBackend": "off"}, data_dir=data_dir)
    assert stub.calls == []
    assert res["backend"] == "off" and res["text"] == ""
    assert "litellm.yaml" in res["error"] and "whisper-cpp" in res["error"]


# ---------------------------------------------------------------- capabilities and the self-test


def test_capability_rows_all_explain_how_to_fix_themselves() -> None:
    data_dir = Path(tempfile.mkdtemp())
    cases = [CFG, {"sttBackend": "off"}, {"sttBackend": "local"}, {"sttBackend": "proxy", "sttModel": ""}, {}]
    for cfg in cases:
        with whisper_is(""), speech_is(False):
            rows = stt.capabilities(cfg, data_dir)
        assert {r["id"] for r in rows} == {"stt", "stt_speech", "stt_local"}, cfg
        for r in rows:
            assert isinstance(r["ok"], bool)
            assert r["detail"]
            assert r["fix"] or r["ok"], (cfg, r)      # anything not ok says how to fix it
    with whisper_is(""), speech_is(False):
        rows = {r["id"]: r for r in stt.capabilities({"sttBackend": "proxy", "sttModel": ""}, data_dir)}
    assert rows["stt"]["ok"] is False
    assert "/v1/audio/transcriptions" in rows["stt"]["fix"]
    assert "brew install whisper-cpp" in rows["stt_local"]["fix"]
    assert str(data_dir / "models" / "ggml-base.en.bin") in rows["stt_local"]["fix"]
    with whisper_is("/opt/homebrew/bin/whisper-cli"), speech_is(False):
        _model(data_dir)
        rows = {r["id"]: r for r in stt.capabilities(CFG, data_dir)}
    assert rows["stt_local"]["ok"] is True and rows["stt_local"]["fix"] == ""
    assert rows["stt"]["ok"] is True and rows["stt"]["detail"].startswith("whisper.cpp at ")


def test_selftest_surfaces_a_proxy_failure() -> None:
    data_dir = Path(tempfile.mkdtemp())
    with proxy_replies(404, None, "404 page not found"), whisper_is(""), speech_is(False):
        res = stt.selftest(settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert set(res) == {"ok", "backend", "record_ms", "transcribe_ms", "text", "error"}
    assert res["ok"] is False
    assert res["error"], res
    assert res["backend"] == "proxy"
    assert res["error"].startswith("transcription 404: ")
    assert res["record_ms"] >= 0


def test_selftest_treats_transcribed_silence_as_a_pass() -> None:
    data_dir = Path(tempfile.mkdtemp())
    # Silence transcribes to "" on every provider; an empty string must not read as a failure.
    with proxy_replies(200, {"text": "", "segments": [], "duration": 0.4}, "") as stub, \
            whisper_is(""), speech_is(False):
        res = stt.selftest(settings=SETTINGS, cfg=CFG, data_dir=data_dir)
    assert res["ok"] is True
    assert res["text"] == "" and res["error"] == ""
    assert res["record_ms"] >= 0
    assert len(stub.calls) == 1
    assert stub.calls[0]["bytes"][:4] == b"RIFF"
    assert len(stub.calls[0]["bytes"]) >= audiocap.MIN_WAV_BYTES
    assert not list(Path(stub.calls[0]["filename"]).parent.glob("stt-selftest-*"))


def test_probes_never_raise_and_never_shell_out_to_a_device() -> None:
    data_dir = Path(tempfile.mkdtemp())
    assert stt.whisper_cli_path() in ("", stt.whisper_cli_path())   # must never raise
    assert stt.BACKENDS == ("auto", "speech", "proxy", "local", "off")
    assert stt.local_model_path(Path("/does/not/exist"), {}) == ""
    assert stt.local_model_path(Path("/etc/hosts"), {}) == ""       # not a directory
    for cfg in ({}, {"sttBackend": None}, {"sttModel": None}, {"whisperModelPath": None}):
        assert stt.resolve_backend(cfg, data_dir) in stt.BACKENDS
        assert len(stt.capabilities(cfg, data_dir)) == 3


def test_local_passes_the_prompt_flag_only_when_given() -> None:
    import subprocess
    data_dir = Path(tempfile.mkdtemp())
    model = data_dir / "m.bin"
    model.write_bytes(b"x")
    argvs: list[list[str]] = []

    def fake_run(argv, **kw):
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="hi", stderr="")

    real = subprocess.run
    subprocess.run = fake_run  # type: ignore[assignment]
    try:
        with whisper_is("/bin/whisper-cli"):
            cfg = {"sttBackend": "local", "whisperModelPath": str(model)}
            stt.transcribe(_wav(), settings=SETTINGS, cfg=cfg, data_dir=data_dir, prompt="Pricing review, Dana")
            stt.transcribe(_wav(), settings=SETTINGS, cfg=cfg, data_dir=data_dir)
    finally:
        subprocess.run = real  # type: ignore[assignment]
    assert argvs[0][argvs[0].index("--prompt") + 1] == "Pricing review, Dana"
    assert "--prompt" not in argvs[1]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
