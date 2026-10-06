"""Validators for the edited arguments of gmail_send / gmail_draft (see approval_edits)."""
from __future__ import annotations

import re
from typing import Any

from .approval_edits import register_validator


class ApprovalEditError(ValueError):
    """An edited mail field is not acceptable; the message becomes the 400 detail."""

MAX_RECIPIENTS = 50
MAX_SUBJECT = 998          # RFC 5322 line limit
MAX_BODY = 200_000
MAX_ADDRESS = 254

_ADDR = re.compile(r"^[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+$")
_NAMED = re.compile(r"^(?P<name>.*?)<(?P<addr>[^<>]+)>$")


def parse_recipients(raw: str) -> list[str]:
    """Split a To field on commas/semicolons/newlines into addresses, keeping a `Name <a@b.c>` form intact.
    Raises ApprovalEditError naming the first entry that is not an address."""
    out: list[str] = []
    # Separators inside a quoted display name ("Doe, John" <j@x.com>) do not split it.
    for part in re.findall(r'(?:"[^"]*"|[^,;\n"])+|"', raw or ""):
        part = part.strip()
        if not part:
            continue
        m = _NAMED.match(part)
        addr = (m.group("addr") if m else part).strip()
        if len(addr) > MAX_ADDRESS or not _ADDR.match(addr):
            raise ApprovalEditError(f"'{part}' is not a valid email address.")
        name = m.group("name").strip().strip('"') if m else ""
        if any(c in name for c in '<>"\r\n'):
            raise ApprovalEditError(f"'{part}' is not a valid recipient.")
        if name and re.search(r"[,;]", name):
            name = f'"{name}"'  # stays one recipient when the list is joined and split again
        out.append(f"{name} <{addr}>" if name else addr)
    return out


def check_send(to: str, subject: str) -> list[str]:
    """The recipients of a send, or ApprovalEditError. Outbox.queue runs it too, so a bad address
    fails before the hold starts rather than when Gmail rejects it later."""
    rcpt = parse_recipients(str(to or ""))
    if not rcpt:
        raise ApprovalEditError("Add at least one recipient.")
    if len(rcpt) > MAX_RECIPIENTS:
        raise ApprovalEditError(f"At most {MAX_RECIPIENTS} recipients.")
    subject = str(subject or "")
    if "\n" in subject or "\r" in subject:  # a newline here is header injection, not a long subject
        raise ApprovalEditError("The subject must be a single line.")
    if len(subject) > MAX_SUBJECT:
        raise ApprovalEditError(f"The subject is over {MAX_SUBJECT} characters.")
    return rcpt


def _clean(args: dict[str, Any], *, allow_draft_flag: bool) -> dict[str, Any]:
    out = dict(args)
    out["to"] = ", ".join(check_send(str(out.get("to") or ""), str(out.get("subject") or "")))
    out["subject"] = str(out.get("subject") or "").strip()
    body = str(out.get("body") or "")
    if len(body) > MAX_BODY:
        raise ApprovalEditError(f"The body is over {MAX_BODY:,} characters.")
    out["body"] = body
    rid = out.get("reply_to_message_id")
    if rid is not None:
        if not isinstance(rid, str) or len(rid) > 128 or not re.fullmatch(r"[\w-]*", rid):
            raise ApprovalEditError("reply_to_message_id is not a Gmail message id.")
        if not rid:
            out.pop("reply_to_message_id")
    if "as_draft" in out:
        if not allow_draft_flag:
            raise ApprovalEditError("as_draft only applies to gmail_send.")
        if not isinstance(out["as_draft"], bool):
            raise ApprovalEditError("as_draft must be true or false.")
        if not out["as_draft"]:
            out.pop("as_draft")
    return out


def _validator(allow_draft_flag: bool):
    """approval_edits' contract: an error string for the 400, or the cleaned arguments."""
    def check(args: dict[str, Any]) -> str | dict[str, Any]:
        try:
            return _clean(args, allow_draft_flag=allow_draft_flag)
        except ApprovalEditError as e:
            return str(e)
    return check


register_validator("gmail_send", _validator(True))
register_validator("gmail_draft", _validator(False))
