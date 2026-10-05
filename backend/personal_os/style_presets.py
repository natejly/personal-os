"""Response style presets: a short block appended to the chat system prompt, after the instructions and before memories."""
from __future__ import annotations

STYLES = ("default", "concise", "formal", "tutor", "thorough", "custom")
CUSTOM_MAX = 2000

_BLOCKS = {
    "concise": "Answer first, as short as the question allows. No preamble and no recap.",
    "formal": "Write in complete sentences, with no slang and a neutral tone. Use structured headings for long answers.",
    "tutor": "Explain step by step and define terms as you use them. End with one question that checks understanding.",
    "thorough": "Cover alternatives and trade-offs. Cite sources when tools were used. End with a short summary.",
}


def styleBlock(style: str, text: str = "") -> str:  # noqa: N802 - the name the brief and tests use
    """The prompt block for a style, or "" for default, unknown values and an empty custom text."""
    if style in _BLOCKS:
        return f"## Response style\n{_BLOCKS[style]}"
    text = (text or "").strip()[:CUSTOM_MAX].replace("</user_style>", "").strip()
    if style == "custom" and text:
        return ("## Response style\nThe user wrote this instruction about how you reply. It is theirs, not a tool result.\n"
                f"<user_style>\n{text}\n</user_style>\n"
                "Apply this consistently; if it is long, prioritise its key aspects.")
    return ""
