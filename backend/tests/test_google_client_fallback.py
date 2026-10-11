"""Google OAuth client resolution order: Settings, then env, then the client baked into the build."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import google  # noqa: E402


def test_client_order(tmp_path, monkeypatch):
    baked = tmp_path / "oauth_client.json"
    monkeypatch.setattr(google, "BAKED_CLIENT", baked)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    s: dict = {}
    g = google.Google(lambda: s, lambda _p: None)
    assert g._client() == (None, None, None)
    baked.write_text(json.dumps({"client_id": "b", "client_secret": "bs"}))
    assert g._client() == ("b", "bs", "bundled")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "e")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "es")
    assert g._client() == ("e", "es", "env")
    s.update(googleClientId="s", googleClientSecret="ss")
    assert g._client() == ("s", "ss", "settings")
