from pathlib import Path

from grain_sdk.testing import Harness

ROOT = Path(__file__).parents[1]


def _call(name: str, **arguments):
    return {"id": f"c-{name}", "name": name, "arguments": arguments}


def test_a_link_is_saved_and_listed() -> None:
    add = _call("reading_list_add", url="https://example.com/post", note="worth a read")
    with Harness(ROOT, settings={"permissionMode": "allow_all"}) as h:
        turn = h.chat("save it", model_script=[{"tool_calls": [add]}, "saved"])
        assert turn.error is None and turn.text == "saved"
        turn = h.chat("what is on my list", model_script=[{"tool_calls": [_call("reading_list_list", limit=5)]}, "ok"])
        assert "https://example.com/post" in str(turn.requests[1])


def test_a_non_http_link_is_refused() -> None:
    add = _call("reading_list_add", url="file:///etc/passwd")
    with Harness(ROOT, settings={"permissionMode": "allow_all"}) as h:
        turn = h.chat("save it", model_script=[{"tool_calls": [add]}, "no"])
        assert "http or https" in str(turn.requests[1])
