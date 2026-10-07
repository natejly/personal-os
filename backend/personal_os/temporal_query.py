"""Find a time window in a query ("last week", "in March 2026", "two weeks ago") so memory ranking can favour it.

Deterministic and conservative: no match beats a wrong match, and the result is only ever a soft ranking
signal (`relevance`), never a filter. Weeks start Monday, seasons are northern-hemisphere, windows are
half-open [start, end) in the timezone of the `now` passed in (use `local_now()`, the Mac's local zone).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MON_RE = "|".join(_MONTHS + ["jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep", "oct", "nov", "dec"])
_AMBIGUOUS = {"may", "march", "mar"}  # also plain English words: need a capital or a preposition
_NUMS = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split())}
_NUM_RE = r"\d{1,3}|" + "|".join(_NUMS)
_YEAR = r"(?:19|20)\d\d"
_PREPS = r"(in|since|after|from|before|until|during|between)"
_SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}  # first month; each lasts 3 months

_ATOM = re.compile("|".join([
    rf"(?P<iso>(?<![\d-])(?P<iy>{_YEAR})-(?P<im>\d\d)-(?P<id>\d\d)(?!\d))",
    rf"(?P<md>\b(?P<mdm>{_MON_RE})\.?\s+(?P<mdd>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<mdy>{_YEAR})\b)?)",
    rf"(?P<dm>(?<![\d:.-])\b(?P<dmd>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<dmm>{_MON_RE})\b(?:,?\s+(?P<dmy>{_YEAR})\b)?)",
    rf"(?P<my>\b(?P<mym>{_MON_RE})\.?,?\s+(?P<myy>{_YEAR})(?![\d]|[-/.]\d))",
    rf"(?P<lm>\b(?P<lmw>last|this|next)\s+(?P<lmm>{_MON_RE})\b)",
    r"(?P<rel>\b(?P<relw>today|yesterday|tomorrow|tonight)\b)",
    r"(?P<uw>\b(?P<uww>this|last|next)\s+(?P<uwu>week|month|year)\b)",
    rf"(?P<ln>\b(?:last|past)\s+(?P<lnn>{_NUM_RE}|couple\s+of|couple)\s+(?P<lnu>day|week|month|year)s?\b)",
    rf"(?P<ago>\b(?P<agn>a\s+couple\s+of|couple\s+of|couple|an?|{_NUM_RE})\s+(?P<agu>day|week|month|year)s?\s+ago\b)",
    r"(?P<sea>\b(?P<seaw>last|this)\s+(?P<seas>spring|summer|fall|autumn|winter)\b)",
    r"(?P<yr>(?<![\d/.:#$-])\b(?P<yrv>20\d\d)(?![\d]|[-/.]\d))",
    rf"(?P<bm>\b(?P<bmm>{_MON_RE})\b)",
]), re.I)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    phrase: str


def local_now() -> datetime:
    return datetime.now().astimezone()


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def _sub_months(d: date, n: int) -> date:  # same day of month, clamped to the month's length
    first = _add_months(d, -n)
    return first.replace(day=min(d.day, (_add_months(first, 1) - first).days))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _month_num(raw: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(raw.lower()[:3]))


def _num(raw: str) -> int:
    raw = raw.lower()
    return 2 if raw.startswith("couple") or raw.startswith("a couple") else 1 if raw in ("a", "an") else \
        _NUMS[raw] if raw in _NUMS else int(raw)


def _unit_window(unit: str, p: date) -> tuple[date, date]:  # the day/week/month/year containing p
    if unit == "day":
        return p, p + timedelta(days=1)
    if unit == "week":
        return _monday(p), _monday(p) + timedelta(days=7)
    if unit == "month":
        return p.replace(day=1), _add_months(p, 1)
    return date(p.year, 1, 1), date(p.year + 1, 1, 1)


def _ambiguous_ok(raw: str, capital_needed: bool) -> bool:
    return not (raw.lower() in _AMBIGUOUS and capital_needed and not raw[0].isupper())


def _season(name: str, which: str, d: date) -> tuple[date, date]:
    m = _SEASONS[name]
    inst = [(date(y, m, 1), _add_months(date(y, m, 1), 3)) for y in range(d.year - 2, d.year + 2)]
    if which == "last":
        return max((i for i in inst if i[1] <= d), key=lambda i: i[1])
    return min(inst, key=lambda i: 0 if i[0] <= d < i[1] else min(abs((i[0] - d).days), abs((i[1] - d).days)))


def _atom(m: re.Match[str], today: date) -> tuple[tuple[date, date] | None, bool]:
    """(date window or None when the phrase is a plain word, needs_preposition)."""
    k = m.lastgroup
    g = m.group
    if k == "iso":
        d = date(int(g("iy")), int(g("im")), int(g("id")))
        return (d, d + timedelta(days=1)), False
    if k in ("md", "dm"):
        mon, day, yr = (g("mdm"), g("mdd"), g("mdy")) if k == "md" else (g("dmm"), g("dmd"), g("dmy"))
        if not _ambiguous_ok(mon, True):
            return None, False
        d = date(int(yr or today.year), _month_num(mon), int(day))
        if not yr and d > today:  # no year: the most recent occurrence
            d = date(d.year - 1, d.month, d.day)
        return (d, d + timedelta(days=1)), False
    if k == "my":
        if not _ambiguous_ok(g("mym"), True):
            return None, False
        f = date(int(g("myy")), _month_num(g("mym")), 1)
        return (f, _add_months(f, 1)), False
    if k == "lm":
        if not _ambiguous_ok(g("lmm"), g("lmw").lower() != "last"):
            return None, False
        mo, w = _month_num(g("lmm")), g("lmw").lower()
        y = today.year - (mo >= today.month) if w == "last" else today.year if w == "this" else \
            today.year + (mo <= today.month)
        f = date(y, mo, 1)
        return (f, _add_months(f, 1)), False
    if k == "rel":
        w = g("relw").lower()
        off = {"today": 0, "tonight": 0, "yesterday": -1, "tomorrow": 1}[w]
        d = today + timedelta(days=off)
        return (d, d + timedelta(days=1)), False
    if k == "uw":
        k_off = {"last": -1, "this": 0, "next": 1}[g("uww").lower()]
        u = g("uwu").lower()
        if u == "week":
            s = _monday(today) + timedelta(days=7 * k_off)
            return (s, s + timedelta(days=7)), False
        if u == "month":
            s = _add_months(today.replace(day=1), k_off)
            return (s, _add_months(s, 1)), False
        return (date(today.year + k_off, 1, 1), date(today.year + k_off + 1, 1, 1)), False
    if k == "ln":
        n, u = _num(g("lnn")), g("lnu").lower()
        s = today - timedelta(days=n * {"day": 1, "week": 7}[u]) if u in ("day", "week") else \
            _sub_months(today, n * (12 if u == "year" else 1))
        return (s, today + timedelta(days=1)), False  # parse clamps the end to now
    if k == "ago":
        n, u = _num(g("agn")), g("agu").lower()
        p = today - timedelta(days=n * {"day": 1, "week": 7}[u]) if u in ("day", "week") else \
            _sub_months(today, n * (12 if u == "year" else 1))
        return _unit_window(u, p), False
    if k == "sea":
        return _season(g("seas").lower(), g("seaw").lower(), today), False
    if k == "yr":
        y = int(g("yrv"))
        return (date(y, 1, 1), date(y + 1, 1, 1)), True
    if not _ambiguous_ok(g("bmm"), False):
        return None, False
    mo = _month_num(g("bmm"))  # bare month: most recent occurrence, this month included
    y = today.year - (mo > today.month)
    return (date(y, mo, 1), _add_months(date(y, mo, 1), 1)), True


def parse(text: str, now: datetime) -> Window | None:
    """The first (then longest) confident time phrase in `text` as a Window, else None.

    "since/from/after X" runs from the start of X to now; "before X" ends where X starts, "until X" where it
    ends (a month or year is included); "between X and Y" and "from X to Y" span both. "tonight" is 18:00 to
    06:00; "last N days" starts at midnight N days back and ends now.
    """
    tz, today = now.tzinfo, now.date()

    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=tz)

    atoms: list[dict] = []
    for m in _ATOM.finditer(text):
        try:
            win, needs_prep = _atom(m, today)
        except ValueError:  # 31 February and friends
            continue
        if win is None:
            continue
        pm = re.search(rf"\b{_PREPS}\s+$", text[:m.start()], re.I)
        w = (at(win[0]), at(win[1]))
        if m.lastgroup == "ln":
            w = (w[0], now)
        elif m.lastgroup == "rel" and m.group("relw").lower() == "tonight":
            w = (w[0].replace(hour=18), at(win[1]).replace(hour=6))
        atoms.append({"s": m.start(), "e": m.end(), "w": w, "need": needs_prep,
                      "prep": pm.group(1).lower() if pm else None, "ps": pm.start() if pm else m.start()})

    found: list[tuple[int, int, tuple[datetime, datetime]]] = []
    used: set[int] = set()
    for i in range(len(atoms) - 1):
        a, b = atoms[i], atoms[i + 1]
        gap = re.fullmatch(r"\s+(and|to|until|through|till)\s+", text[a["e"]:b["s"]], re.I)
        if gap and (a["prep"] == "between" and gap.group(1).lower() == "and"
                    or a["prep"] == "from" and gap.group(1).lower() != "and"):
            found.append((a["ps"], b["e"], (min(a["w"][0], b["w"][0]), max(a["w"][1], b["w"][1]))))
            used |= {i, i + 1}
    far = at(_sub_months(today, 240))
    for i, a in enumerate(atoms):
        p, (s, e) = a["prep"], a["w"]
        if i in used or p == "between" or (a["need"] and p is None):
            continue
        if p in ("since", "from", "after"):
            if s >= now:
                continue
            e = now
        elif p == "before":
            s, e = far, s
        elif p == "until":
            s = far
        found.append((a["ps"] if p else a["s"], a["e"], (s, e)))
    if not found:
        return None
    s0, e0, (s, e) = min(found, key=lambda f: (f[0], f[0] - f[1]))
    return Window(s, e, text[s0:e0])


def relevance(window: Window, ts: float) -> float:
    """1.0 inside the window, falling linearly to 0 one window-width outside either edge."""
    lo, hi = window.start.timestamp(), window.end.timestamp()
    if lo <= ts < hi:
        return 1.0
    return max(0.0, 1.0 - ((lo - ts) if ts < lo else (ts - hi)) / max(hi - lo, 1.0))
