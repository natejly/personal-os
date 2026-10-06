"""Named redaction rules: one copy of the patterns, selectable by subset.

One list of credential patterns for everything that must not echo a secret. `scrub_secrets` keeps
the rules that catch credentials and drops the two (email, phone) that would destroy a conversation.
"""
from __future__ import annotations

import re
from typing import Iterable

# Anything matching these is scrubbed out of text before it is stored or shown to the model.
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
    "google_oauth": (re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}\b"), "[google-access]"),
    "slack_webhook": (re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"), "[slack-webhook]"),
    "jwt": (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[jwt]"),
    "phone": (re.compile(r"\b(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), "[phone]"),
    # Long unbroken mixed-case-and-digit runs: almost never prose, often a credential.
    "entropy": (re.compile(r"\b(?=[A-Za-z0-9+/=_-]*\d)(?=[A-Za-z0-9+/=_-]*[A-Za-z])[A-Za-z0-9+/=_-]{28,}\b"), "[redacted]"),
}

# A word that announces a secret, plus whatever follows it - that is where the value lives.
# Scoped to the value rather than the whole line on purpose: a long single-line buffer
# would lose thousands of harmless characters to one word if the whole line went.
SECRET_ASSIGN = re.compile(
    r"(?:password|passwd|passphrase|secret|token|api[ _-]?key|access[ _-]?key|credit ?card|cvv|"
    r"pin ?code|seed phrase)\s*(?:is|are|=|:)?\s*\S{0,64}",
    re.I,
)

ALL_RULES: tuple[str, ...] = tuple(RULES)

# Credentials only. `email` and `phone` are deliberately absent: replacing every address with [email]
# and every phone-shaped run of digits with [phone] would erase who is who in the text.
# A leaked API key is a breach; a colleague's email address is the point.
SECRET_RULES: tuple[str, ...] = (
    "private_key", "url_userinfo", "url_secret_param", "card", "ssn", "token", "aws_key",
    "github_pat", "google_api", "google_oauth", "slack_webhook", "jwt", "entropy",
)

# The rules in application order.
REDACTIONS: list[tuple[re.Pattern[str], str]] = [RULES[k] for k in ALL_RULES]


def scrub(text: str, rules: Iterable[str] = ALL_RULES, secret_assign: bool = True) -> str:
    """Apply the named rules in the order given, then the announce-a-secret sweep.

    Unknown names are skipped rather than raised on: callers pass rule sets built from
    settings, and a stale name must not break the caller.
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


# What a command or script may print back into the model. No entropy or card rules: those eat
# paths, commit hashes and build ids, which are most of that output.
COMMAND_OUTPUT_RULES: tuple[str, ...] = (
    "url_userinfo", "url_secret_param", "private_key", "aws_key", "jwt", "token",
    "github_pat", "google_api", "google_oauth", "slack_webhook",
)


def scrub_command_output(text: str) -> str:
    """Strip credentials from stdout/stderr before it is shown to the model."""
    return scrub(text or "", COMMAND_OUTPUT_RULES, secret_assign=False)


def scrub_secrets(text: str) -> str:
    """Strip credentials, keep the people."""
    return scrub(text, SECRET_RULES, secret_assign=True)


# ---------------------------------------------------------------- v2: validated, scored spans
#
# Propose-validate-score with the stdlib: a regex proposes, a validator checks, nearby words nudge the
# score, a threshold decides. Everything above is untouched; `scrub`/`scrub_secrets` keep their
# exact behaviour. `sanitize_url` (MCP import) is built on `analyze`.

import math  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Callable  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

THRESHOLD = 0.4
CONTEXT_WINDOW = 40

CONTEXT_WORDS: dict[str, tuple[str, ...]] = {
    "card": ("card", "visa", "mastercard", "amex", "cc", "payment"),
    "phone": ("phone", "tel", "call", "mobile", "cell"),
    "ssn": ("ssn", "social"),
    "entropy": ("bearer", "authorization", "secret", "key", "token"),
    "token": ("bearer", "authorization", "secret", "key"),
}
# Words that say a long hex run is a hash, not a credential.
_HASH_WORDS = ("commit", "sha", "hash", "checksum", "digest", "revision")
# Rules whose pattern is specific enough to trust without a validator.
_SELF_VALIDATING = {"private_key", "aws_key", "jwt", "token", "email"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def luhn(s: str) -> bool:
    d = _digits(s)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _phone_ok(s: str) -> bool:
    d = _digits(s)
    if len(d) == 11:
        if d[0] != "1":
            return False
        d = d[1:]
    elif len(d) == 12 and s.lstrip().startswith("+"):
        d = d[2:]               # the pattern admits a 2-digit country code
    elif len(d) != 10:
        return False
    if len(set(d)) == 1:
        return False
    # NANP area codes start 2-9; that also rejects epoch-seconds-looking runs (1727800000).
    return d[0] in "23456789"


def _ssn_ok(s: str) -> bool:
    d = _digits(s)
    if len(d) != 9:
        return False
    area, group, serial = d[:3], d[3:5], d[5:]
    return not (area in ("000", "666") or area[0] == "9" or group == "00" or serial == "0000")


def shannon(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def _entropy_ok(s: str) -> bool:
    return shannon(s) >= 3.5


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "card": luhn,
    "phone": _phone_ok,
    "ssn": _ssn_ok,
    "entropy": _entropy_ok,
}


@dataclass
class Span:
    start: int
    end: int
    entity: str
    score: float
    replacement: str


def _context_before(text: str, start: int, words: tuple[str, ...]) -> bool:
    if not words:
        return False
    window = text[max(0, start - CONTEXT_WINDOW):start].lower()
    return any(re.search(r"\b" + re.escape(w) + r"\b", window) for w in words)


def compile_patterns(items: Iterable[str]) -> list[str | re.Pattern[str]]:
    """User list entries: a plain string, or /regex/. A bad regex is skipped, never raised."""
    out: list[str | re.Pattern[str]] = []
    for raw in items or []:
        s = str(raw).strip()
        if not s:
            continue
        if len(s) > 2 and s.startswith("/") and s.endswith("/"):
            try:
                out.append(re.compile(s[1:-1], re.I))
            except re.error:
                continue
        else:
            out.append(s)
    return out


def _allowed(span_text: str, allow: list[str | re.Pattern[str]]) -> bool:
    low = span_text.strip().lower()
    for a in allow:
        if isinstance(a, str):
            if a.lower() == low:
                return True
        elif a.search(span_text):
            return True
    return False


def analyze(text: str, rules: Iterable[str] = ALL_RULES, *, allow: Iterable[str] = (),
            deny: Iterable[str] = (), threshold: float = THRESHOLD) -> list[Span]:
    """Scored spans for `text`, overlaps resolved, sorted by position."""
    if not text:
        return []
    allow_c, deny_c = compile_patterns(allow), compile_patterns(deny)
    cands: list[Span] = []
    for name in rules:
        rule = RULES.get(name)
        if rule is None:
            continue
        validator = VALIDATORS.get(name)
        for m in rule[0].finditer(text):
            frag = m.group(0)
            if _allowed(frag, allow_c):
                continue
            ctx = _context_before(text, m.start(), CONTEXT_WORDS.get(name, ()))
            if validator is None:
                score = 0.85 if name in _SELF_VALIDATING else 0.5
                if ctx:
                    score = min(1.0, score + 0.35)
            else:
                ok = validator(frag)
                hash_like = (name == "entropy" and re.fullmatch(r"[0-9a-fA-F]+", frag) is not None
                             and _context_before(text, m.start(), _HASH_WORDS))
                if hash_like:
                    continue    # a commit hash is long, random-looking and harmless
                if ok:
                    score = min(1.0, 0.8 + (0.35 if ctx else 0.0))
                elif ctx:
                    score = 0.5
                else:
                    continue
            if score >= threshold:
                cands.append(Span(m.start(), m.end(), name, round(score, 3), rule[1]))
    for d in deny_c:
        it = re.finditer(re.escape(d), text, re.I) if isinstance(d, str) else d.finditer(text)
        for m in it:
            if m.end() > m.start():
                cands.append(Span(m.start(), m.end(), "custom", 1.0, "[redacted]"))
    cands.sort(key=lambda s: (-s.score, -(s.end - s.start), s.start))
    kept: list[Span] = []
    for s in cands:
        if all(s.end <= k.start or s.start >= k.end for k in kept):
            kept.append(s)
    kept.sort(key=lambda s: s.start)
    return kept


def scrub_v2(text: str, rules: Iterable[str] = ALL_RULES, allow: Iterable[str] = (),
             deny: Iterable[str] = (), counts: dict[str, int] | None = None,
             threshold: float = THRESHOLD) -> str:
    """`scrub` with validation, context and allow/deny lists. Counts entities, never content."""
    if not text:
        return ""
    out = text
    for s in reversed(analyze(text, rules, allow=allow, deny=deny, threshold=threshold)):
        out = out[:s.start] + s.replacement + out[s.end:]
        if counts is not None:
            counts[s.entity] = counts.get(s.entity, 0) + 1
    n_secret = 0

    def _sweep(_m: re.Match[str]) -> str:
        nonlocal n_secret
        n_secret += 1
        return "[secret]"

    out = SECRET_ASSIGN.sub(_sweep, out)
    if counts is not None and n_secret:
        counts["secret"] = counts.get("secret", 0) + n_secret
    return out


_SENSITIVE_KEY = re.compile(r"token|code|key|secret|auth|sig|password|session|access|state|jwt|otp", re.I)
_SECRETISH_SEGMENT = re.compile(r"^(?=[A-Za-z0-9_]*\d)(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9_]{24,}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def sanitize_url(url: str) -> str:
    """scheme + host + path, query keys kept but sensitive values blanked, no userinfo or fragment."""
    try:
        if not isinstance(url, str) or not url.strip():
            return ""
        sp = urlsplit(url.strip())
        if not sp.scheme or not sp.netloc:
            return ""
        host = sp.netloc.rpartition("@")[2]
        if not host:
            return ""
        segs = []
        for seg in sp.path.split("/"):
            if seg and (_SECRETISH_SEGMENT.match(seg) or _UUID.match(seg)
                        or analyze(seg, ("entropy", "token", "jwt"))):
                seg = ":redacted"
            segs.append(seg)
        out = f"{sp.scheme}://{host}{'/'.join(segs)}"
        parts = []
        for pair in sp.query.split("&"):
            if not pair:
                continue
            k, eq, v = pair.partition("=")
            if not eq:
                parts.append(k[:32])
                continue
            v = "~" if (_SENSITIVE_KEY.search(k) or analyze(v)) else v[:32]
            parts.append(f"{k[:64]}={v}")
        if parts:
            out += "?" + "&".join(parts)
        return out
    except Exception:  # noqa: BLE001 - a malformed URL must never take a collector down
        return ""
