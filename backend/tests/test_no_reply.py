"""The NO_REPLY marker: recognised however it is dressed, stripped from the edges of a real reply."""
from personal_os import workers as W


def test_is_silent_tolerates_markdown_quotes_punctuation() -> None:
    for t in ("NO_REPLY", "no_reply", "_NO_REPLY_", '"NO_REPLY"', "“NO_REPLY”", "NO_REPLY!", "> NO_REPLY", "\n`NO_REPLY`\n"):
        assert W.is_silent(t), t
    for t in ("NO_REPLY, but X", "Done.", "NO REPLY needed", "say NO_REPLY"):
        assert not W.is_silent(t), t


def test_strip_no_reply() -> None:
    assert W.strip_no_reply("NO_REPLY") == ""
    assert W.strip_no_reply(None) == ""
    assert W.strip_no_reply("Done.\n\nNO_REPLY") == "Done."
    assert W.strip_no_reply("**NO_REPLY**\nDone.") == "Done."
    assert W.strip_no_reply("A\nNO_REPLY\nB") == "A\nNO_REPLY\nB"  # only the edges
    assert W.strip_no_reply("  Hello  ") == "Hello"


def test_a_reply_cut_off_inside_the_marker_is_silent() -> None:
    for t in ("NO_", "no_rep", "NO_REPL", " `NO_RE` ", "NO_R"):
        assert W.is_silent(t), t
        assert W.strip_no_reply(t) == "", t
    for t in ("NO", "No.", "NO_REPORT", "NO_REPX", "NOT"):
        assert not W.is_silent(t), t
    assert W.strip_no_reply("Done.\nNO_REP") == "Done."
