"""Telegram reply formatting: markdown to HTML, the plain fallback, and splitting that never cuts a tag, an entity or a code block."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import telegram_format as tf  # noqa: E402


def well_formed(h: str) -> bool:
    """Every tag closed in order, no bare < or &, no entity cut short."""
    stack: list[str] = []
    for m in re.finditer(r"<(/?)([a-z]+)(?: [^>]*)?>", h):
        if not m.group(1):
            stack.append(m.group(2))
        else:
            assert stack and stack.pop() == m.group(2), h[:200]
    assert not stack, h[:200]
    text = re.sub(r"<[^>]*>", "", h)
    assert "<" not in text and ">" not in text
    assert not re.search(r"&(?!(amp|lt|gt|quot);)", text), text[:200]
    return True


# ---------------------------------------------------------------- rendering

def test_text_is_escaped() -> None:
    r = tf.render("a < b & c > d")
    assert r.html == "a &lt; b &amp; c &gt; d"
    assert r.plain == "a < b & c > d"


def test_inline_markup() -> None:
    h = tf.render("**bold** __also__ *it* _too_ ~~gone~~ `x < y`").html
    assert h == "<b>bold</b> <b>also</b> <i>it</i> <i>too</i> <s>gone</s> <code>x &lt; y</code>"


def test_underscores_in_words_and_addresses_are_not_italics() -> None:
    assert tf.render("use snake_case_name and https://a.co/x_y_z now").html == "use snake_case_name and https://a.co/x_y_z now"


def test_emphasis_around_code_and_mis_nesting() -> None:
    assert tf.render("**see `a`**").html == "<b>see <code>a</code></b>"
    h = tf.render("*a **b* c**").html  # crossed emphasis: falls back to text, never a broken tag
    assert well_formed(h) and "<" not in h


def test_links_images_and_bare_urls() -> None:
    assert tf.render("[docs](https://x.co/a?b=1&c=2)").html == '<a href="https://x.co/a?b=1&amp;c=2">docs</a>'
    assert tf.render("[https://x.co](https://x.co)").html == "https://x.co"
    assert tf.render("![alt](https://x.co/i.png)").html == "https://x.co/i.png"
    assert tf.render('[q](https://x.co/a"b)').html == '<a href="https://x.co/a&quot;b">q</a>'
    assert tf.render("[bad](javascript:alert(1))").html == "bad (javascript:alert(1))"
    assert tf.render("[**b** link](https://x.co)").html == '<a href="https://x.co"><b>b</b> link</a>'


def test_headings_lists_quotes_rules() -> None:
    h = tf.render("# Big **title**\n- one\n* two\n+ three\n  - nested\n1. first\n2. second\n> quoted *text*\n> more\n\n---").html
    assert h == ("<b>Big title</b>\n• one\n• two\n• three\n  • nested\n1. first\n2. second\n"
                 "<blockquote>quoted <i>text</i>\nmore</blockquote>\n\n──────────")


def test_fenced_code_is_escaped_and_has_no_other_markup() -> None:
    r = tf.render("before\n```python\nx = **1** < 2 & `y`\n```\nafter")
    assert r.html == 'before\n<pre><code class="language-python">x = **1** &lt; 2 &amp; `y`</code></pre>\nafter'
    assert tf.render("```\nplain\n```").html == "<pre>plain</pre>"
    assert tf.render("~~~sh\nls\n~~~").html == '<pre><code class="language-sh">ls</code></pre>'
    assert tf.render('```a"><b\nx\n```').html == '<pre><code class="language-ab">x</code></pre>'  # a language name cannot inject markup
    assert tf.render("```\n```").html == ""
    assert well_formed(tf.render("```py\nunclosed < fence").html)  # an unclosed fence runs to the end


def test_blank_lines_collapse_but_code_is_untouched() -> None:
    assert tf.render("a\n\n\n\nb").html == "a\n\nb"
    assert tf.render("```\na\n\n\n\nb\n```").html == "<pre>a\n\n\n\nb</pre>"


def test_small_table_is_a_monospace_block() -> None:
    r = tf.render("| Name | Qty |\n|------|----:|\n| **apple** | 3 |\n| fig | 12 |")
    assert r.html == "<pre>Name  | Qty\n------+----\napple | 3\nfig   | 12</pre>"
    assert r.files == []


def test_wide_table_becomes_a_csv() -> None:
    head = "| " + " | ".join(f"column{i}" for i in range(8)) + " |\n|" + "---|" * 8
    row = "| " + " | ".join(f"value{i}" for i in range(8)) + " |"
    r = tf.render(f"intro\n\n{head}\n{row}\n\noutro")
    assert r.html == f"intro\n\n{tf.TABLE_PLACEHOLDER}\n\noutro"
    [(name, mime, data)] = r.files
    assert (name, mime) == ("table.csv", "text/csv")
    assert data.decode().splitlines() == [",".join(f"column{i}" for i in range(8)), ",".join(f"value{i}" for i in range(8))]


def test_long_table_becomes_a_csv_and_cells_keep_commas_and_pipes() -> None:
    rows = "\n".join(f"| {i} | a, b \\| c |" for i in range(41))
    r = tf.render(f"| n | t |\n|---|---|\n{rows}")
    assert r.html == tf.TABLE_PLACEHOLDER and len(r.files) == 1
    assert r.files[0][2].decode().splitlines()[1] == '0,"a, b | c"'
    assert tf.render("| n | t |\n|---|---|\n" + "\n".join(f"| {i} | x |" for i in range(40))).files == []  # 40 rows still fit


def test_two_wide_tables_get_two_files() -> None:
    wide = "| " + " | ".join("c" * 12 for _ in range(6)) + " |\n|" + "---|" * 6 + "\n"
    r = tf.render(wide + "\ntext\n\n" + wide)
    assert [f[0] for f in r.files] == ["table.csv", "table-2.csv"]


def test_pipes_without_a_separator_row_are_text() -> None:
    assert tf.render("a | b\nc | d").html == "a | b\nc | d"


def test_plain_is_the_old_to_plain_and_still_works() -> None:
    md = "# H\n- one\n- **two** and `code`\n```py\nx = 1\n```\n> quote"
    assert tf.render(md).plain == tf.to_plain(md) == "H\n• one\n• two and code\nx = 1\nquote"
    assert tf.to_plain("") == "" and tf.render("").html == ""


def test_rendered_output_is_well_formed_and_deterministic() -> None:
    md = ("# T\n\nSome **bold *nested* text**, `code`, [l](https://a.co) & <script>alert(1)</script>.\n\n"
          "> q\n\n- a\n- b\n\n```js\nif (a < b && c > d) {}\n```\n\n| a | b |\n|--|--|\n| 1 | 2 |\n\n_end_")
    r = tf.render(md)
    assert well_formed(r.html) and r.html == tf.render(md).html and "<script>" not in r.html


def test_html_to_plain() -> None:
    h = tf.render("**a** &amp; [site](https://x.co) `c<d` ![i](https://x.co/i.png)").html
    assert tf.html_to_plain(h) == "a &amp; site (https://x.co) c<d https://x.co/i.png"
    assert tf.html_to_plain("<b>x</b> &lt; y &amp; z") == "x < y & z"


# ---------------------------------------------------------------- splitting

def test_split_html_small_is_unchanged_and_blank_is_empty() -> None:
    assert tf.split_html("<b>hi</b>") == ["<b>hi</b>"] and tf.split_html("  ") == []
    assert tf.split_plain("hello") == ["hello"] and tf.split_plain("") == []


def test_split_prefers_paragraph_line_sentence_word() -> None:
    a, b = "a" * 3000, "b" * 3000
    assert tf.split_plain(f"{a}\n\n{b}") == [a, b]
    assert tf.split_plain(f"{a}\n{b}") == [a, b]
    assert tf.split_plain(f"{a}. {b}") == [f"{a}.", b]
    assert tf.split_plain(f"{a} {b}") == [a, b]
    assert [len(p) for p in tf.split_plain("x" * 5000)] == [4096, 904]
    assert tf.split_html(f"<b>{a}</b>\n\n<i>{b}</i>") == [f"<b>{a}</b>", f"<i>{b}</i>"]


def test_split_never_cuts_a_tag_or_an_entity() -> None:
    # every character is an entity or sits inside a tag: any cut that is not on a token boundary would show up
    h = "".join(f'<a href="https://x.co/{i}">l&amp;{i}&lt;</a>' for i in range(900))
    parts = tf.split_html(h, 200)
    assert len(parts) > 10 and all(len(p) <= 200 and well_formed(p) for p in parts)
    assert "".join(re.sub(r"<[^>]*>", "", p) for p in parts) == re.sub(r"<[^>]*>", "", h)


def test_split_inside_formatting_closes_and_reopens() -> None:
    h = "<b>" + "word " * 2000 + "</b>"
    parts = tf.split_html(h)
    assert len(parts) == 3 and all(p.startswith("<b>") and p.endswith("</b>") and len(p) <= 4096 for p in parts)
    assert " ".join(" ".join(re.sub(r"<[^>]*>", "", p) for p in parts).split()) == ("word " * 2000).strip()


def test_split_reopens_a_code_fence() -> None:
    body = "\n".join(f"line {i} < {i + 1} && x" for i in range(800))
    h = tf.render(f"intro\n\n```py\n{body}\n```\n\nafter **done**").html
    parts = tf.split_html(h)
    assert len(parts) >= 3 and all(len(p) <= 4096 and well_formed(p) for p in parts)
    assert parts[0].startswith("intro\n\n<pre><code")
    assert all(p.startswith('<pre><code class="language-py">') for p in parts[1:-1])  # reopened with the same language
    assert parts[0].endswith("</code></pre>") and parts[-1].endswith("after <b>done</b>")
    seen = [l for p in parts for l in html_lines(p) if l.startswith("line ")]
    assert seen == [f"line {i} < {i + 1} && x" for i in range(800)]  # every code line exactly once, cut only between lines


def html_lines(p: str) -> list[str]:
    return tf.html_to_plain(p).split("\n")


def test_split_plain_keeps_angle_brackets() -> None:
    parts = tf.split_plain("<b>" + "x" * 5000)
    assert "".join(parts) == "<b>" + "x" * 5000


def test_split_never_truncates() -> None:
    parts = tf.split_plain("para one\n\n" * 20000)
    assert len(parts) > 8 and all(len(p) <= 4096 for p in parts)


# ---------------------------------------------------------------- long replies

def test_a_short_reply_is_sent_as_parts() -> None:
    p = tf.plan("hello **world**")
    assert p.parts == ["hello <b>world</b>"] and p.summary is None and p.files == []


def test_a_long_reply_becomes_a_summary() -> None:
    first = "First sentence is here. " + "More words follow in this paragraph. " * 40
    p = tf.plan(first + "\n\n" + "Another paragraph. " * 400)
    assert p.parts == [] and p.summary and p.summary.endswith("\n\n" + tf.SUMMARY_TAIL)
    body = p.summary[:-len(tf.SUMMARY_TAIL) - 2]
    assert len(body) <= tf.SUMMARY_MAX and body.endswith(".") and first.startswith(body)


def test_summary_of_one_long_unbroken_paragraph_cuts_at_a_word() -> None:
    p = tf.plan("word " * 2000)
    body = p.summary[:-len(tf.SUMMARY_TAIL) - 2]
    assert len(body) <= tf.SUMMARY_MAX + 1 and body.endswith("…")


def test_exactly_at_the_threshold_still_sends_parts() -> None:
    assert tf.plan("a" * tf.LONG_REPLY_CHARS).summary is None
    assert tf.plan("a" * (tf.LONG_REPLY_CHARS + 1)).summary is not None


def test_a_table_left_out_does_not_count_toward_the_length() -> None:
    rows = "\n".join(f"| {i} | {'x' * 200} |" for i in range(41))
    p = tf.plan(f"| n | t |\n|---|---|\n{rows}")
    assert p.summary is None and p.parts == [tf.TABLE_PLACEHOLDER] and len(p.files) == 1
