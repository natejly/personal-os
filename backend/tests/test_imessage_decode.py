"""attributedBody decoding, handle normalisation, markdown flattening, reply splitting and command parsing (imessage.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import imessage as im  # noqa: E402

HEAD = (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00"
        b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+")
HEAD_MUTABLE = (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00"
                b"\x85\x92\x84\x84\x84\x0fNSMutableString\x01\x84\x84\x08NSString\x01\x94\x84\x01+")
TAIL = (b"\x86\x84\x02iI\x01\x0b\x92\x84\x84\x84\x0cNSDictionary\x01\x94\x84\x01i\x01\x92\x84\x96\x96\x1d"
        b"__kIMMessagePartAttributeName\x86\x86\x86")


def blob(text: str, head: bytes = HEAD) -> bytes:
    raw = text.encode()
    if len(raw) < 0x80:
        n = bytes([len(raw)])
    elif len(raw) < 0x10000:
        n = b"\x81" + len(raw).to_bytes(2, "little")
    else:
        n = b"\x82" + len(raw).to_bytes(4, "little")
    return head + n + raw + TAIL


def test_short_ascii() -> None:
    assert im.decode_attributed_body(blob("hello there")) == "hello there"


def test_multibyte_and_emoji() -> None:
    s = "café 🎉 日本語"
    assert im.decode_attributed_body(blob(s)) == s


def test_two_byte_length() -> None:
    s = "x" * 200
    b = blob(s)
    assert b"\x81\xc8\x00" in b
    assert im.decode_attributed_body(b) == s


def test_four_byte_length() -> None:
    s = ("abc " * 17500).strip()  # 69,999 chars
    assert len(s) > 65535
    b = blob(s)
    assert b"\x82" in b
    assert im.decode_attributed_body(b) == s


def test_mutable_string_variant() -> None:
    assert im.decode_attributed_body(blob("mutable body", HEAD_MUTABLE)) == "mutable body"


def test_object_replacement_chars_and_whitespace_stripped() -> None:
    assert im.decode_attributed_body(blob("￼ check this ￼ ")) == "check this"
    assert im.decode_attributed_body(blob("￼")) is None  # an attachment-only message has no text


def test_malformed_input_is_none() -> None:
    good = blob("hello there")
    assert im.decode_attributed_body(None) is None
    assert im.decode_attributed_body(b"") is None
    assert im.decode_attributed_body(b"not a typedstream at all") is None
    assert im.decode_attributed_body(good[:len(HEAD) + 4]) is None  # truncated mid-string
    assert im.decode_attributed_body(HEAD) is None  # marker, no length
    assert im.decode_attributed_body(HEAD + b"\x82\x01") is None  # truncated length
    assert im.decode_attributed_body(HEAD + b"\x90rest") is None  # unknown length tag
    assert im.decode_attributed_body(b"NSString" + b"\x00" * 60 + b"+\x03abc") is None  # + too far from the marker


def test_normalize_handle() -> None:
    n = im.normalize_handle
    assert n("(555) 123-4567") == "+15551234567"
    assert n("555.123.4567") == "+15551234567"
    assert n("1 555 123 4567") == "+15551234567"
    assert n("+15551234567") == "+15551234567"
    assert n("+44 20 7946 0958") == "+442079460958"
    assert n("  Nate@Example.COM ") == "nate@example.com"
    for bad in ("-5551234567", "-a@b.co", "", "   ", "nate@", "@x.com", "a b@c.d", "call me 5551234567", "12345", "+1234567890123456", None, 5):
        assert n(bad) is None, bad


def test_mask_handle_hides_the_number_and_the_address() -> None:
    assert im.mask_handle("+15551234567") == "…4567"
    assert im.mask_handle("nate@example.com") == "n…@…com"
    assert im.mask_handle(None) == "?"
    assert "5551" not in im.mask_handle("+15551234567") and "example" not in im.mask_handle("nate@example.com")


def test_to_plain() -> None:
    md = ("# Title\n\nSome **bold**, *italic*, __also bold__, ~~gone~~ and `code`.\n\n> quoted\n\n- one\n* two\n\n"
          "```python\nprint('a*b*')\n```\n\n[docs](https://x.dev/a) and [https://y.dev](https://y.dev)\n![alt](https://i.dev/p.png)\n\n\n\n\nend")
    out = im.to_plain(md)
    assert out == ("Title\n\nSome bold, italic, also bold, gone and code.\n\nquoted\n\n• one\n• two\n\n"
                   "print('a*b*')\n\ndocs (https://x.dev/a) and https://y.dev\nhttps://i.dev/p.png\n\nend")
    assert "snake_case_name" in im.to_plain("keep snake_case_name intact")


def test_split_reply_short_is_untouched() -> None:
    assert im.split_reply("short") == ["short"]
    assert im.split_reply("   ") == []


def test_split_reply_paragraphs_and_markers() -> None:
    paras = [("word " * 100).strip() for _ in range(6)]  # ~500 chars each
    parts = im.split_reply("\n\n".join(paras), size=1500)
    assert len(parts) == 2 or len(parts) == 3
    assert parts[0].startswith(f"(1/{len(parts)}) ")
    assert all(len(p) <= 1500 for p in parts)
    assert "\n\n".join(p.split(") ", 1)[1] for p in parts).replace("\n\n", " ").split() == " ".join(paras).split()


def test_split_reply_hard_boundary_without_spaces() -> None:
    parts = im.split_reply("x" * 4000, size=1500, max_parts=4)
    assert len(parts) == 3 and all(len(p) <= 1500 for p in parts)


def test_split_reply_caps_parts() -> None:
    parts = im.split_reply(("sentence number one. " * 400), size=1500, max_parts=4)
    assert len(parts) == 4
    assert parts[-1].endswith(im.TRUNCATED) and all(len(p) <= 1500 for p in parts)
    assert parts[0].startswith("(1/4) ") and parts[3].startswith("(4/4) ")


def test_parse_command() -> None:
    p = im.parse_command
    assert p("status") == ("status", None) and p("  STOP. ") == ("stop", None) and p("New!") == ("new", None)
    assert p("help") == ("help", None)
    for w in ("yes", "y", "Approve", "YES!"):
        assert p(w) == ("approve", None), w
    for w in ("no", "n", "deny", "No."):
        assert p(w) == ("deny", None), w
    assert p("yes 3") == ("approve", "3") and p("no 12.") == ("deny", "12") and p("approve 4") == ("approve", "4")
    for other in ("yes please", "status 3", "stop it", "what's the status", "", "yes 3 4", "hello"):
        assert p(other) is None, other


def test_rate_limiter_warns_once_then_recovers() -> None:
    t = [0.0]
    rl = im.RateLimiter(3, 60, clock=lambda: t[0])
    assert [rl.check("a") for _ in range(3)] == ["ok"] * 3
    assert rl.check("a") == "limited_first" and rl.check("a") == "limited"
    assert rl.check("b") == "ok"  # per handle
    t[0] = 61
    assert rl.check("a") == "ok"
