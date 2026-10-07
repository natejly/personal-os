"""Outlook Mail over Microsoft Graph, returning the dicts `Google`'s gmail_* methods return.

`MailMixin` is mixed into `Microsoft`, which supplies `_req(method, path, params=, json=, headers=)`,
`me` and `get_settings`. Every HTTP call goes through `_req`. Callers (the Mail view, outbox.py,
mailwatch.py, the gmail_* tools) see the same keys whichever provider is active, so the Gmail names
(`thread_id`, `labels` holding INBOX / UNREAD / STARRED) are synthesised from Graph fields.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import re
from email.utils import getaddresses
from typing import Any

from . import verify
from .cache import cached, invalidates
from .google import GoogleNotConnected, NotSent

TTL = {"gmail_list": 60, "gmail_threads": 120, "gmail_message": 15 * 60, "gmail_labels": 10 * 60}

# Gmail label id -> Graph well-known folder. Insertion order is the order the label list shows them.
_FOLDERS = {"INBOX": "inbox", "SENT": "sentitems", "DRAFT": "drafts", "ARCHIVE": "archive", "SPAM": "junkemail", "TRASH": "deleteditems"}
# Words the Mail view / agent put after `in:` or `label:`.
_FOLDER_WORDS = {"inbox": "inbox", "sent": "sentitems", "drafts": "drafts", "draft": "drafts", "trash": "deleteditems",
                 "spam": "junkemail", "archive": "archive"}
_LIST_SELECT = "id,conversationId,subject,from,toRecipients,receivedDateTime,sentDateTime,bodyPreview,isRead,flag,hasAttachments,categories,parentFolderId"
_THREAD_SELECT = _LIST_SELECT + ",ccRecipients,isDraft,internetMessageHeaders"
_TOKEN = re.compile(r'-?[A-Za-z_]+:(?:"[^"]*"|\S+)|"[^"]*"|\S+')
_AGE = {"h": "hours", "d": "days", "w": "weeks"}


def _q(s: str) -> str:
    """A string literal for a Graph $filter."""
    return "'" + s.replace("'", "''") + "'"


def _parse(query: str) -> dict[str, Any]:
    """The few Gmail query terms the app sends, as structured filters plus leftover free text.

    Negations (-category:promotions, -category:social, -in:spam) are dropped: Outlook has no promotions
    or social tabs, and the threads reader already skips junk and deleted mail itself.
    """
    p: dict[str, Any] = {"folder": None, "unread": False, "starred": False, "attach": False, "since": None,
                         "from": [], "labels": [], "text": []}
    for tok in _TOKEN.findall(query or ""):
        if tok.startswith("-"):
            continue
        key, sep, val = tok.partition(":")
        key, val = key.lower(), val.strip('"')
        if not sep or key not in ("is", "in", "has", "newer_than", "from", "label"):
            p["text"].append(tok)  # subject:invoice and plain words go to $search, which reads that syntax
        elif key == "is" and val == "unread":
            p["unread"] = True
        elif key == "is" and val == "starred":
            p["starred"] = True
        elif key == "has" and val == "attachment":
            p["attach"] = True
        elif key in ("in", "label") and val.lower() in _FOLDER_WORDS:
            p["folder"] = _FOLDER_WORDS[val.lower()]
        elif key == "label":
            p["labels"].append(val)
        elif key == "newer_than":
            m = re.fullmatch(r"(\d+)([hdwmy])", val.lower())
            if m:
                n, u = int(m.group(1)), m.group(2)
                delta = dt.timedelta(**{_AGE[u]: n}) if u in _AGE else dt.timedelta(days=n * (30 if u == "m" else 365))
                p["since"] = (dt.datetime.now(dt.timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif key == "from":
            (p["from"] if "@" in val else p["text"]).append(val if "@" in val else f"from:{val}")
    return p


def _recipient(r: dict[str, Any] | None) -> str | None:
    ea = (r or {}).get("emailAddress") or {}
    addr, name = ea.get("address"), ea.get("name")
    if not addr:
        return None
    if not name or name == addr:
        return addr
    return f'"{name}" <{addr}>' if re.search(r'[,;<>"]', name) else f"{name} <{addr}>"


def _recipients(rs: list[dict[str, Any]] | None) -> str | None:
    out = [a for a in (_recipient(r) for r in rs or []) if a]
    return ", ".join(out) or None


def _to_graph(to: str) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": addr, **({"name": name} if name else {})}} for name, addr in getaddresses([to or ""]) if addr]


def _addrs(rs: list[dict[str, Any]] | None) -> list[str]:
    return sorted(((r.get("emailAddress") or {}).get("address") or "").lower() for r in rs or [])


def _text(content: str, kind: str) -> str:
    """Body text. Asked for as text already; the strip is for the HTML Graph sends anyway."""
    if kind != "html":
        return (content or "").strip()
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content or "", flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", _html.unescape(t))).strip()


def _when(m: dict[str, Any]) -> str:
    return m.get("receivedDateTime") or m.get("sentDateTime") or ""


def _keep(m: dict[str, Any], p: dict[str, Any], cats: list[str]) -> bool:
    """The structured terms, applied client-side: Graph will not take $filter beside $search."""
    sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
    return (not (p["unread"] and m.get("isRead")) and not (p["starred"] and (m.get("flag") or {}).get("flagStatus") != "flagged")
            and not (p["attach"] and not m.get("hasAttachments")) and not (p["since"] and _when(m) < p["since"])
            and all(a.lower() == sender for a in p["from"]) and all(c in (m.get("categories") or []) for c in cats))


class MailMixin:
    # ---------- helpers ----------
    def _folder_labels(self) -> dict[str, str]:
        """{folder id: Gmail label id} for the well-known folders. Ids never change, so it is read once."""
        known = self.__dict__.get("_ms_folders")
        if known is None:
            known = {}
            for label, name in _FOLDERS.items():
                try:
                    known[(self._req("GET", f"/me/mailFolders/{name}", params={"$select": "id"}) or {}).get("id")] = label
                except Exception as e:  # noqa: BLE001
                    if getattr(e, "status", None) is None:
                        raise  # not connected / network: only a Graph answer (archive absent) is skippable
            known.pop(None, None)
            self.__dict__["_ms_folders"] = known
        return known

    def _label_ids(self, m: dict[str, Any]) -> list[str]:
        out = []
        folder = self._folder_labels().get(m.get("parentFolderId"))
        if folder:
            out.append(folder)
        if not m.get("isRead"):
            out.append("UNREAD")
        if (m.get("flag") or {}).get("flagStatus") == "flagged":
            out.append("STARRED")
        return out + list(m.get("categories") or [])

    def _query(self, query: str, want: int, select: str) -> list[dict[str, Any]]:
        p = _parse(query)
        want = max(1, min(int(want), 500))
        cats = []
        if p["labels"]:  # Gmail writes a label's spaces as hyphens, so match against the real category names
            names = [c.get("displayName") or "" for c in (self._req("GET", "/me/outlook/masterCategories") or {}).get("value") or []]
            cats = [next((n for n in names if re.sub(r"\s+", "-", n).lower() == lab.lower()), lab) for lab in p["labels"]]
        path = f"/me/mailFolders/{p['folder']}/messages" if p["folder"] else "/me/messages"
        params: dict[str, Any] = {"$select": select}
        if p["text"]:
            params["$search"] = '"' + " ".join(p["text"]).replace('"', "") + '"'
            params["$top"] = min(want * 4, 250)  # ponytail: structured terms filter this page client-side, so it is over-fetched; a rare match can still fall outside it
        else:
            # The orderby property has to lead the filter, so a date term is always present (epoch when none was asked for).
            f = [f"receivedDateTime ge {p['since'] or '1970-01-01T00:00:00Z'}"]
            f += ["isRead eq false"] * p["unread"] + ["flag/flagStatus eq 'flagged'"] * p["starred"] + ["hasAttachments eq true"] * p["attach"]
            f += [f"from/emailAddress/address eq {_q(a)}" for a in p["from"]] + [f"categories/any(c:c eq {_q(c)})" for c in cats]
            params.update({"$filter": " and ".join(f), "$orderby": "receivedDateTime desc", "$top": want})
        rows = (self._req("GET", path, params=params) or {}).get("value") or []
        if p["text"]:
            rows = [m for m in rows if _keep(m, p, cats)]
        rows.sort(key=_when, reverse=True)
        return rows[:want]

    # ---------- reads ----------
    @cached("gmail", TTL["gmail_list"])
    def gmail_search(self, query: str = "is:unread in:inbox newer_than:14d", max_results: int = 15) -> list[dict[str, Any]]:
        return [{"id": m.get("id"), "thread_id": m.get("conversationId"), "from": _recipient(m.get("from")), "subject": m.get("subject"),
                 "date": _when(m) or None, "snippet": m.get("bodyPreview"), "unread": not m.get("isRead"), "labels": self._label_ids(m)}
                for m in self._query(query, max_results, _LIST_SELECT)]

    @cached("gmail", TTL["gmail_threads"])
    def gmail_threads_recent(self, query: str = "newer_than:14d -category:promotions -category:social -in:spam", max_threads: int = 40) -> list[dict[str, Any]]:
        """Recent threads as metadata only, messages oldest to newest. Graph has no thread listing, so
        this reads recent messages across all folders and groups them by conversationId."""
        threads: dict[str, list[dict[str, Any]]] = {}
        for m in self._query(query, max(1, min(int(max_threads), 100)) * 6, _THREAD_SELECT):
            labels = self._label_ids(m)
            if m.get("isDraft") or "SPAM" in labels or "TRASH" in labels:
                continue
            h = {(x.get("name") or "").lower(): x.get("value") or "" for x in m.get("internetMessageHeaders") or []}
            auto = (bool(h.get("list-unsubscribe")) or h.get("precedence", "").strip().lower() in ("bulk", "list", "junk")
                    or h.get("auto-submitted", "no").strip().lower() != "no")
            threads.setdefault(m.get("conversationId") or m["id"], []).append(
                {"id": m.get("id"), "from": _recipient(m.get("from")), "to": _recipients(m.get("toRecipients")), "cc": _recipients(m.get("ccRecipients")),
                 "date": _when(m) or None, "labels": labels, "snippet": m.get("bodyPreview") or "", "auto": auto, "_subject": m.get("subject")})
        out = []
        for tid, msgs in threads.items():
            msgs.sort(key=lambda x: x["date"] or "")
            subject = next((x["_subject"] for x in msgs if x["_subject"]), "") or ""
            for x in msgs:
                x.pop("_subject", None)
            out.append({"thread_id": tid, "subject": subject, "messages": msgs})
        out.sort(key=lambda t: t["messages"][-1]["date"] or "", reverse=True)
        return out[:max(1, min(int(max_threads), 100))]

    @cached("gmail", TTL["gmail_message"])
    def gmail_get(self, message_id: str, max_chars: int = 8000) -> dict[str, Any]:
        m = self._req("GET", f"/me/messages/{message_id}", params={"$select": "id,conversationId,subject,from,toRecipients,receivedDateTime,sentDateTime,body"},
                      headers={"Prefer": 'outlook.body-content-type="text"'}) or {}
        b = m.get("body") or {}
        return {"id": message_id, "thread_id": m.get("conversationId"), "from": _recipient(m.get("from")), "to": _recipients(m.get("toRecipients")),
                "subject": m.get("subject"), "date": _when(m) or None, "body": _text(b.get("content", ""), b.get("contentType", "text").lower())[:max_chars]}

    @cached("gmail", TTL["gmail_labels"])
    def gmail_labels(self) -> list[dict[str, Any]]:
        """Well-known folders as system labels, Outlook categories as user labels."""
        have = set(self._folder_labels().values())
        labels = [{"id": k, "name": k, "type": "system"} for k in _FOLDERS if k in have]
        cats = (self._req("GET", "/me/outlook/masterCategories") or {}).get("value") or []
        labels += [{"id": c["displayName"], "name": c["displayName"], "type": "user"} for c in cats if c.get("displayName")]
        return sorted(labels, key=lambda x: (x["type"] != "system", x["name"].lower()))

    # ---------- writes ----------
    def _message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        return {"subject": subject, "body": {"contentType": "Text", "content": body}, "toRecipients": _to_graph(to)}

    def _reply_draft(self, reply_to_message_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
        """A reply draft in the original's conversation, carrying the caller's recipients, subject and body."""
        d = self._req("POST", f"/me/messages/{reply_to_message_id}/createReply") or {}
        try:
            self._req("PATCH", f"/me/messages/{d['id']}", json=self._message(to, subject, body))
        except Exception:
            try:
                self._req("DELETE", f"/me/messages/{d['id']}")
            except Exception:  # noqa: BLE001
                pass
            raise
        return d

    def _read_message(self, message_id: str, what: str, select: str) -> dict[str, Any]:
        try:
            return self._req("GET", f"/me/messages/{message_id}", params={"$select": select}) or {}
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status", None) == 404:
                raise verify.NotVisible(f"{what} {message_id} is not in the mailbox") from e
            raise

    @invalidates("gmail")
    def gmail_draft(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None,
                    attachments: list[dict[str, Any]] | None = None, cc: str | None = None,
                    bcc: str | None = None) -> dict[str, Any]:
        if attachments or cc or bcc:
            raise ValueError("Attachments, Cc and Bcc are not supported for Outlook mail yet.")
        d = (self._reply_draft(reply_to_message_id, to, subject, body) if reply_to_message_id
             else self._req("POST", "/me/messages", json=self._message(to, subject, body))) or {}
        did = d.get("id") or ""
        want_to = _addrs(_to_graph(to))
        out = {"draft_id": did, "to": to, "subject": subject, "note": "Draft saved in Outlook; not sent."}

        def read_back() -> dict[str, Any]:
            if not did:
                raise verify.NotVisible("Graph returned no draft id")
            return self._read_message(did, "draft", "subject,toRecipients,isDraft")

        def compare(m: dict[str, Any]) -> dict[str, Any]:
            diff = verify.diff({"subject": subject}, {"subject": m.get("subject")})
            if want_to and _addrs(m.get("toRecipients")) != want_to:
                diff["to"] = {"expected": ", ".join(want_to), "actual": ", ".join(_addrs(m.get("toRecipients")))}
            return diff

        return verify.attach(out, verify.check(f"draft {did} in Outlook", read_back, compare=compare, compared=["exists", "subject", "to"]))

    @invalidates("gmail")
    def gmail_send(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None,
                   attachments: list[dict[str, Any]] | None = None, cc: str | None = None,
                   bcc: str | None = None) -> dict[str, Any]:
        """Send now. Callers go through outbox.py instead, which holds the send so it can be undone.

        Anything that fails before Graph is asked to send is raised as NotSent (or not-connected, kept as is):
        nothing went out. Once sendMail/send is called its 202 carries no message, so the read-back looks the
        message up in Sent Items.
        """
        if attachments or cc or bcc:
            raise NotSent("Attachments, Cc and Bcc are not supported for Outlook mail yet.")
        want_to = _addrs(_to_graph(to))
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        thread_id: str | None = None
        try:
            if not want_to:
                raise ValueError("no recipient address")
            if reply_to_message_id:
                d = self._reply_draft(reply_to_message_id, to, subject, body)
                thread_id = d.get("conversationId")
            else:
                msg = self._message(to, subject, body)
        except GoogleNotConnected:
            raise
        except Exception as e:  # noqa: BLE001
            raise NotSent(f"{type(e).__name__}: {e}") from e
        if reply_to_message_id:
            self._req("POST", f"/me/messages/{d['id']}/send")
        else:
            self._req("POST", "/me/sendMail", json={"message": msg, "saveToSentItems": True})
        found: dict[str, Any] = {}

        def read_back() -> dict[str, Any]:
            res = self._req("GET", "/me/mailFolders/sentitems/messages", params={
                "$select": "id,conversationId,subject,toRecipients",
                "$filter": f"sentDateTime ge {since} and subject eq {_q(subject)}", "$orderby": "sentDateTime desc", "$top": 1}) or {}
            rows = res.get("value") or []
            if not rows:
                raise verify.NotVisible("the message is not in Sent Items yet")
            found.update(rows[0])
            return rows[0]

        def compare(m: dict[str, Any]) -> dict[str, Any]:
            diff = verify.diff({"thread_id": thread_id, "subject": subject}, {"thread_id": m.get("conversationId"), "subject": m.get("subject")})
            if _addrs(m.get("toRecipients")) != want_to:
                diff["to"] = {"expected": ", ".join(want_to), "actual": ", ".join(_addrs(m.get("toRecipients")))}
            return diff

        v = verify.check("sent message in Outlook Sent Items", read_back, compare=compare, compared=["Sent Items", "thread_id", "subject", "to"],
                         delays=verify.MAIL_RETRY_DELAYS)
        return verify.attach({"sent": found.get("id"), "to": to, "subject": subject, "thread_id": found.get("conversationId") or thread_id}, v)

    @invalidates("gmail")
    def gmail_modify(self, message_id: str, mark_read: bool | None = None, archive: bool = False, star: bool | None = None) -> dict[str, Any]:
        add, rem, patch = [], [], {}
        if mark_read is not None:
            patch["isRead"] = mark_read
            (rem if mark_read else add).append("UNREAD")
        if star is not None:
            patch["flag"] = {"flagStatus": "flagged" if star else "notFlagged"}
            (add if star else rem).append("STARRED")
        if archive:
            rem.append("INBOX")
        if patch:
            self._req("PATCH", f"/me/messages/{message_id}", json=patch)
        mid = message_id
        if archive:  # a moved message gets a new id
            mid = (self._req("POST", f"/me/messages/{message_id}/move", json={"destinationId": "archive"}) or {}).get("id") or message_id

        def read_back() -> dict[str, Any]:
            return self._read_message(mid, "message", "isRead,flag,parentFolderId,categories")

        def compare(m: dict[str, Any]) -> dict[str, Any]:
            have, out = set(self._label_ids(m)), {}
            out.update({f"+{x}": {"expected": "present", "actual": "missing"} for x in add if x not in have})
            out.update({f"-{x}": {"expected": "removed", "actual": "still set"} for x in rem if x in have})
            return out

        v = verify.check(f"labels on message {mid}", read_back, compare=compare, compared=[f"+{x}" for x in add] + [f"-{x}" for x in rem] or ["labels"])
        out = {"ok": True, "added": add, "removed": rem}
        return verify.attach({**out, "moved_id": mid} if mid != message_id else out, v)
