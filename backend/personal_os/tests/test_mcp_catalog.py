"""The connector catalog: validation rules and install rendering. No network, no app.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_catalog.py -q
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from personal_os import mcp_catalog


def entry(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "demo", "name": "Demo", "description": "A demo connector.", "category": "Developer", "icon": "database",
        "publisher": "Acme", "official": True, "docs": "https://example.com/docs", "transport": "stdio", "runtime": "node",
        "install": {"command": "npx", "args": ["-y", "demo-mcp", "{folder}"], "env": {"DEMO_TOKEN": "{token}", "DEMO_REGION": "{region}"}},
        "auth": "api_key",
        "fields": [{"id": "token", "label": "Token", "secret": True, "required": True},
                   {"id": "region", "label": "Region", "default": "eu"},
                   {"id": "folder", "label": "Folder", "required": False}],
        "verified": {"source": "npm", "ref": "demo-mcp", "version": "1.0.0", "on": "2026-10-06"},
    }
    return {**base, **over}


def doc(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "entries": list(entries)}


def errors(**over: Any) -> list[str]:
    return mcp_catalog.validate(doc(entry(**over)))


def test_a_good_entry_validates() -> None:
    assert mcp_catalog.validate(doc(entry())) == []


def test_the_bundled_catalog_validates_when_present() -> None:
    if not mcp_catalog.BUNDLED.exists():
        pytest.skip("no bundled catalog yet")
    assert mcp_catalog.validate(json.loads(mcp_catalog.BUNDLED.read_text())) == []


def test_a_secret_field_may_not_appear_in_args_command_or_url() -> None:
    e = entry()
    e["install"]["args"] = ["-y", "demo-mcp", "--token={token}"]
    assert any("secret field token appears in install.args" in x for x in mcp_catalog.validate(doc(e)))
    r = entry(transport="http", runtime="remote", install={"url": "https://x.example/{token}"}, auth="api_key")
    assert any("install.url" in x for x in mcp_catalog.validate(doc(r)))
    c = entry()
    c["install"]["command"] = "run-{token}"
    assert any("install.command" in x for x in mcp_catalog.validate(doc(c)))


def test_a_secret_field_in_a_header_is_fine() -> None:
    e = entry(transport="http", runtime="remote", auth="api_key",
              install={"url": "https://api.example.com/mcp", "headers": {"Authorization": "Bearer {token}"}},
              fields=[{"id": "token", "label": "Key", "secret": True, "required": True}])
    assert mcp_catalog.validate(doc(e)) == []


@pytest.mark.parametrize("over,needle", [
    ({"id": "Bad Id"}, "id must be"),
    ({"category": "Nope"}, "category"),
    ({"description": "x" * 201}, "description"),
    ({"transport": "carrier-pigeon"}, "transport"),
    ({"docs": "http://insecure.example"}, "docs"),
    ({"icon": "Not An Icon"}, "icon"),
    ({"verified": {"source": "rumour", "ref": "x", "on": "2026-10-06"}}, "verified"),
    ({"auth": "none"}, "auth none cannot have secret fields"),
    ({"transport": "stdio", "install": {"command": "", "args": []}}, "needs install.command"),
    ({"transport": "stdio", "install": {"command": "npx", "url": "https://x.example"}}, "must not have install.url"),
])
def test_rule_violations_are_reported(over: dict[str, Any], needle: str) -> None:
    assert any(needle in x for x in errors(**over)), errors(**over)


def test_remote_entries_need_an_https_url_and_no_command() -> None:
    e = entry(transport="http", runtime="remote", fields=[], auth="none", install={"url": "http://x.example/mcp"})
    assert any("https://" in x for x in mcp_catalog.validate(doc(e)))
    e = entry(transport="http", runtime="remote", fields=[], auth="none", install={"url": "https://x.example/mcp", "command": "npx"})
    assert any("must not have install.command" in x for x in mcp_catalog.validate(doc(e)))


def test_oauth_needs_a_remote_transport() -> None:
    assert any("oauth" in x for x in errors(auth="oauth"))


def test_placeholders_must_be_declared_and_required_fields_used() -> None:
    e = entry()
    e["install"]["env"]["X"] = "{ghost}"
    assert any("{ghost}" in x for x in mcp_catalog.validate(doc(e)))
    e = entry()
    e["fields"].append({"id": "unused", "label": "Unused", "required": True})
    assert any("required field unused" in x for x in mcp_catalog.validate(doc(e)))


def test_a_secret_default_and_a_literal_credential_are_rejected() -> None:
    e = entry()
    e["fields"][0]["default"] = "abc"
    assert any("must not have a default" in x for x in mcp_catalog.validate(doc(e)))
    e = entry()
    e["install"]["env"]["LITERAL"] = "ghp_" + "a" * 36
    assert any("literal credential" in x for x in mcp_catalog.validate(doc(e)))


def test_an_ordinary_flag_is_not_mistaken_for_a_credential() -> None:
    e = entry()
    e["install"]["args"] = ["-y", "demo-mcp", "--api-endpoint-override-for-testing", "ghcr.io/org/some-long-server-image-name"]
    assert mcp_catalog.validate(doc(e)) == []


def test_duplicate_ids_and_bad_documents() -> None:
    assert any("duplicate id" in x for x in mcp_catalog.validate(doc(entry(), entry())))
    assert mcp_catalog.validate({"entries": []})
    assert mcp_catalog.validate("nope")


def test_multiple_is_only_for_whole_arguments() -> None:
    e = entry(fields=[{"id": "token", "label": "T", "secret": True, "required": True}, {"id": "folder", "label": "F", "multiple": True}])
    e["install"]["args"] = ["-y", "x", "--dir={folder}"]
    assert any("multiple field folder" in x for x in mcp_catalog.validate(doc(e)))


# ---------- render_install ----------
def test_render_splits_secrets_from_env_and_drops_an_empty_optional_arg() -> None:
    out = mcp_catalog.render_install(entry(), {"token": "test-token-123"})
    assert out["secrets"] == {"DEMO_TOKEN": "test-token-123"}
    assert out["env"] == {"DEMO_REGION": "eu"}  # the default fills in
    assert out["args"] == ["-y", "demo-mcp"]     # {folder} was empty and optional
    assert out["catalog_id"] == "demo" and out["command"] == "npx" and out["transport"] == "stdio"
    out = mcp_catalog.render_install(entry(), {"token": "t", "folder": "/tmp/x", "region": "us"})
    assert out["args"] == ["-y", "demo-mcp", "/tmp/x"] and out["env"] == {"DEMO_REGION": "us"}


def test_missing_required_names_the_field_not_the_value() -> None:
    with pytest.raises(ValueError) as exc:
        mcp_catalog.render_install(entry(), {"token": "   ", "region": "sekret-region"})
    assert "token" in str(exc.value) and "sekret-region" not in str(exc.value)


def test_multiple_splits_on_newlines_and_commas() -> None:
    e = entry(fields=[{"id": "token", "label": "T", "secret": True, "required": True},
                      {"id": "folder", "label": "F", "multiple": True}])
    out = mcp_catalog.render_install(e, {"token": "t", "folder": "/a, /b\n/c\n\n"})
    assert out["args"] == ["-y", "demo-mcp", "/a", "/b", "/c"]


def test_a_header_template_with_a_secret_lands_in_headers() -> None:
    e = entry(transport="http", runtime="remote", auth="api_key",
              install={"url": "https://{host}/mcp", "headers": {"Authorization": "Bearer {key}", "X-Empty": "{opt}"}},
              fields=[{"id": "key", "label": "K", "secret": True, "required": True},
                      {"id": "host", "label": "H", "default": "api.example.com"}, {"id": "opt", "label": "O"}])
    out = mcp_catalog.render_install(e, {"key": "test-token-123"})
    assert out["headers"] == {"Authorization": "Bearer test-token-123"}  # an empty optional header is dropped
    assert out["url"] == "https://api.example.com/mcp" and out["command"] == "" and out["args"] == []


def test_a_value_containing_braces_is_not_expanded_again() -> None:
    e = entry()
    out = mcp_catalog.render_install(e, {"token": "t", "region": "{token}"})
    assert out["env"] == {"DEMO_REGION": "{token}"}


def test_load_reads_a_given_path_and_withholds_invalid_entries(tmp_path: Path) -> None:
    bad = copy.deepcopy(entry(id="leaky"))
    bad["install"]["args"] = ["--token={token}"]
    f = tmp_path / "cat.json"
    f.write_text(json.dumps(doc(entry(), bad)))
    ids = [e["id"] for e in mcp_catalog.load(f)["entries"]]
    assert ids == ["demo"]
    assert mcp_catalog.get("demo", f)["name"] == "Demo" and mcp_catalog.get("leaky", f) is None
    assert mcp_catalog.load(tmp_path / "missing.json")["entries"] == []
