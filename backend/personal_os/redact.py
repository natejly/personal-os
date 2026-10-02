"""Named redaction rules: one copy of the patterns, selectable by subset.

Lifted out of activity.py's gate so meetings can reuse the credential patterns without
hand-copying a security-relevant list that would then drift. activity.Gate.scrub is
`scrub(text)` with the defaults, byte for byte; meetings call `scrub_secrets`, which keeps
the rules that catch credentials and drops the two that destroy a conversation.
"""
from __future__ import annotations

import re
from typing import Iterable

# Anything matching these is scrubbed out of typed text and transcripts before it is stored.
# Deliberately blunt: a false positive costs a few characters of context, a miss stores a secret.
# Insertion order IS the application order, and `REDACTIONS` below depends on it.
RULES: dict[str, tuple[re.Pattern[str], str]] = {
    "private_key": (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[private-key]"),
    # https://user:password@host and ?token= / #access_token= never match the prefixed-key rules.
    "url_userinfo": (re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1[redacted]@"),
    "url_secret_param": (re.compile(
        r"([?#&](?:access_token|refresh_token|id_token|client_secret|api_key|apikey|password|passwd|"
        r"secret|signature|token|auth|key|sig)=)[^&#\s]+", re.I), r"\1[redacted]"),
    "email": (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    "card": (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[card-number]"),
    "ssn": (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    "token": (re.compile(r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|ghu|ghs|ghr|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b", re.I), "[token]"),
    "aws_key": (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
    "github_pat": (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[github-pat]"),
    "google_api": (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[google-key]"),
    "slack_webhook": (re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"), "[slack-webhook]"),
    "jwt": (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[jwt]"),
    "phone": (re.compile(r"\b(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), "[phone]"),
    # Long unbroken mixed-case-and-digit runs: almost never prose, often a credential.
    "entropy": (re.compile(r"\b(?=[A-Za-z0-9+/=_-]*\d)(?=[A-Za-z0-9+/=_-]*[A-Za-z])[A-Za-z0-9+/=_-]{28,}\b"), "[redacted]"),
}

# A word that announces a secret, plus whatever follows it - that is where the value lives.
# Scoped to the value rather than the whole line on purpose: typed text arrives as one long
# single-line buffer, so dropping the line would throw away thousands of harmless characters
# because of one word.
SECRET_ASSIGN = re.compile(
    r"(?:password|passwd|passphrase|secret|token|api[ _-]?key|access[ _-]?key|credit ?card|cvv|"
    r"pin ?code|seed phrase)\s*(?:is|are|=|:)?\s*\S{0,64}",
    re.I,
)

ALL_RULES: tuple[str, ...] = tuple(RULES)

# Credentials only. `email` and `phone` are deliberately absent: a meeting transcript is a
# record of who said what to whom, and the gate's identity rules replace every address with
# [email] and every phone-shaped run of digits with [phone]
# (redact.py), which would erase attendee identity from inside the conversation.
# A leaked API key is a breach; a colleague's email address in their own meeting is the point.
SECRET_RULES: tuple[str, ...] = (
    "private_key", "url_userinfo", "url_secret_param", "card", "ssn", "token", "aws_key",
    "github_pat", "google_api", "slack_webhook", "jwt", "entropy",
)

# The exact object activity.py used to define at module level, re-exported so nothing that
# iterated it has to change.
REDACTIONS: list[tuple[re.Pattern[str], str]] = [RULES[k] for k in ALL_RULES]


def scrub(text: str, rules: Iterable[str] = ALL_RULES, secret_assign: bool = True) -> str:
    """Apply the named rules in the order given, then the announce-a-secret sweep.

    Unknown names are skipped rather than raised on: callers pass rule sets built from
    settings, and a stale name must not take a collector down.
    """
    if not text:
        return ""
    out = text
    for name in rules:
        rule = RULES.get(name)
        if rule is None:
            continue
        out = rule[0].sub(rule[1], out)
    if secret_assign:
        # Whatever follows a word like "password" is almost certainly the value itself.
        out = SECRET_ASSIGN.sub("[secret]", out)
    return out


def scrub_secrets(text: str) -> str:
    """What the meetings feature calls: strip credentials, keep the people."""
    return scrub(text, SECRET_RULES, secret_assign=True)
