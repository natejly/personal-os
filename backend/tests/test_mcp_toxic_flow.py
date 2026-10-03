from personal_os import mcp_eval


def _codes(tools):
    return [f["code"] for f in mcp_eval.evaluate_tools(tools)["findings"]]


def test_reader_plus_sender_warns() -> None:
    tools = [{"name": "read_file", "description": "Read a file"}, {"name": "send_email", "description": "Send a message"}]
    rep = mcp_eval.evaluate_tools(tools)
    hit = [f for f in rep["findings"] if f["code"] == "toxic_flow"]
    assert len(hit) == 1 and hit[0]["severity"] == "warn"


def test_reads_only_is_quiet() -> None:
    assert "toxic_flow" not in _codes([{"name": "read_file", "description": "Read a file"},
                                       {"name": "list_notes", "description": "List notes"}])


def test_get_email_is_a_reader_not_a_writer() -> None:
    assert "toxic_flow" not in _codes([{"name": "get_email", "description": "Get an email"},
                                       {"name": "read_file", "description": "Read a file"}])


def test_one_tool_is_not_a_pair() -> None:
    assert "toxic_flow" not in _codes([{"name": "send_email", "description": "Send a message"}])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
