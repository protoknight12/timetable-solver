"""BE-21: deterministic helpers around the LLM (plain Python, no Flask/DB).

The model proposes; these helpers (1) find in the admin's text which teachers, classes,
subjects, clock times and days are mentioned, so the model gets *hints* it can check
instead of guessing, and (2) let ``app.llm`` reject a proposal that points at a person
or class the text does not name. Nothing here creates a constraint.

Everything is heuristic and deliberately conservative: when a mention can mean several
entries we return all of them, and the caller treats that as "ambiguous".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time

DAYS = {
    1: "понеделник",
    2: "вторник",
    3: "сряда",
    4: "четвъртък",
    5: "петък",
    6: "събота",
    7: "неделя",
}
# Prefixes of the weekday words in any inflection ("в петък", "петъците", "срядата").
_DAY_STEMS = (
    ("понеделни", 1),
    ("вторник", 2),
    ("вторници", 2),
    ("сряд", 3),
    ("четвърт", 4),
    ("петък", 5),
    ("петъц", 5),
    ("петъч", 5),
    ("събот", 6),
    ("недел", 7),
)

_LATIN_TO_CYRILLIC = str.maketrans(
    "abcekmhopxtyABCEKMHOPXTY", "авсекмнорхтуАВСЕКМНОРХТУ"
)


def norm(text: str) -> str:
    """Lower-case, Latin look-alikes -> Cyrillic (e.g. a Latin "a" in "11a")."""
    return text.translate(_LATIN_TO_CYRILLIC).lower().replace("ѝ", "и")


def tokens(text: str) -> list[str]:
    return re.findall(r"[а-яa-z0-9]+\.?", norm(text))


# --------------------------------------------------------------------------
# Teachers
# --------------------------------------------------------------------------

_DEFINITE = ("ите", "ата", "ият", "ия", "ът", "ят", "ото")


_GENDER_ENDINGS = (
    ("ова", "ов"),
    ("ева", "ев"),
    ("ина", "ин"),
    ("ска", "ск"),
    ("ски", "ск"),
    ("ови", "ов"),
    ("еви", "ев"),
    ("ини", "ин"),
    ("ову", "ов"),
)


def surname_key(word: str) -> str:
    """Canonical form of a surname so that Иванов / Иванова / Ивановите / Иванови match.

    Heuristic for the usual Bulgarian -ов/-ев/-ин/-ски surnames; others only match
    when equal. The result is used for candidates, never as proof.
    """
    w = norm(word).strip(".")
    for suffix in _DEFINITE:
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[: -len(suffix)]
            break
    for ending, base in _GENDER_ENDINGS:
        if w.endswith(ending) and len(w) - len(ending) >= 2:
            return w[: -len(ending)] + base
    return w


def _first_key(word: str) -> str:
    return norm(word).strip(".")[:4]


def _given_name_matches(token: str, teacher: dict) -> bool:
    """An initial ("ал.") or a first name next to the surname fits this teacher."""
    first = norm(teacher["name"].split()[0])
    bare = token.strip(".")
    if token.endswith(".") and bare:
        return first.startswith(bare)
    return len(bare) >= 3 and _first_key(bare) == first[:4]


@dataclass(frozen=True)
class Mention:
    text: str
    ids: tuple[int, ...]  # all entries this text may mean (one = unambiguous)


def find_teachers(text: str, teachers: list[dict]) -> list[Mention]:
    """``teachers``: dicts with id, name. One Mention per surname found in ``text``."""
    toks = tokens(text)
    by_key: dict[str, list[dict]] = {}
    for t in teachers:
        parts = t["name"].split()
        if len(parts) >= 2:
            by_key.setdefault(surname_key(parts[-1]), []).append(t)
    found: list[Mention] = []
    for i, tok in enumerate(toks):
        key = surname_key(tok)
        if len(key) < 4 or key not in by_key:
            continue
        cands = by_key[key]
        # Narrow by a first name or an initial next to the surname ("Ив. Иванов").
        near = [toks[j] for j in (i - 1, i + 1) if 0 <= j < len(toks)]
        narrowed = [t for t in cands if any(_given_name_matches(n, t) for n in near)]
        chosen = narrowed or cands
        found.append(Mention(tok, tuple(t["id"] for t in chosen)))
    return found


def teacher_verdict(teacher_id: int, mentions: list[Mention]) -> str | None:
    """None if the text names exactly this teacher, else a Bulgarian reason."""
    groups = [m for m in mentions if teacher_id in m.ids]
    if any(len(m.ids) == 1 for m in groups):
        return None
    if groups:
        return "Има няколко учители с това име. Посочете първо име или инициал."
    return "Учителят не е назован в текста."


# --------------------------------------------------------------------------
# Classes
# --------------------------------------------------------------------------

_ORDINALS = {
    "седм": 7,
    "осм": 8,
    "девет": 9,
    "десет": 10,
    "единайсет": 11,
    "единадесет": 11,
    "дванайсет": 12,
    "дванадесет": 12,
}
_ROMAN = {"vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}
_ORD = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_CLASS_NAME = re.compile(r"^\s*(\d{1,2})\s*[-–]?\s*([а-я])")


def class_key(name: str) -> tuple[int, str] | None:
    """ "11 А", "11a", "11А (ИТ)" -> (11, "а")."""
    m = _CLASS_NAME.match(norm(name))
    return (int(m.group(1)), m.group(2)) if m else None


def find_classes(text: str, classes: list[dict]) -> tuple[list[int], list[int]]:
    """Return (class ids, grades) mentioned in ``text``.

    Accepts "11а", "11 А", "11-а", "единайсети а", "единадесетите класове".
    A digit + letter only counts when such a class exists ("2 и" is not a class).
    """
    t = norm(text)
    by_key = {class_key(c["name"]): c["id"] for c in classes if class_key(c["name"])}
    grades_present = {k[0] for k in by_key}
    ids: list[int] = []
    grades: list[int] = []

    def add_class(grade: int, letter: str) -> bool:
        cid = by_key.get((grade, letter))
        if cid is not None and cid not in ids:
            ids.append(cid)
        return cid is not None

    for m in re.finditer(
        r"(?<!\d)(\d{1,2})(?:-?(?:ти|ви|ри|ми|и))?\s*[- ]?\s*([а-я])\b", t
    ):
        add_class(int(m.group(1)), m.group(2))
    for m in re.finditer(rf"\b({_ORD})\w*\s+([а-я])\b", t):
        add_class(_ORDINALS[m.group(1)], m.group(2))
    for m in re.finditer(
        r"\b(vii|viii|ix|xii|xi|x)\s*[- ]?\s*([а-яa-z])\b", text.lower()
    ):
        add_class(_ROMAN[m.group(1)], norm(m.group(2)))
    # Whole grades: "11 клас", "единайсетите класове", "всички десети".
    for m in re.finditer(r"(?<!\d)(\d{1,2})(?:-?\w{0,3})?\s+клас", t):
        grades.append(int(m.group(1)))
    for m in re.finditer(rf"\b({_ORD})\w*\s+клас", t):
        grades.append(_ORDINALS[m.group(1)])
    grades = [g for g in dict.fromkeys(grades) if g in grades_present]
    return ids, grades


# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------

# Everyday abbreviations/nicknames -> a phrase that appears in the official name.
# General school jargon, not school data; the match is still checked against the
# subjects that really exist.
SUBJECT_ALIASES = {
    "бел": "български език и литература",
    "фвс": "физическо възпитание",
    "физкултура": "физическо възпитание",
    "ит": "информационни технологии",
    "ае": "английски",
}
_STOP = {"и", "на", "за", "по", "в", "с", "от", "до", "към"}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[а-яa-z0-9]+", norm(text)) if w]


def find_subjects(text: str, subjects: list[dict]) -> list[Mention]:
    """``subjects``: dicts with id, name, short_name (may be None)."""
    t = norm(text)
    req_words = _words(t)
    req_set = set(req_words)
    found: list[Mention] = []

    def phrase_in(phrase: str) -> bool:
        pw = _words(phrase)
        n = len(pw)
        return n > 0 and any(
            req_words[i : i + n] == pw for i in range(len(req_words) - n + 1)
        )

    # 1. short names and aliases, as whole words/phrases ("ФВС", "ЧЕ по професията")
    for s in subjects:
        short = (s.get("short_name") or "").strip()
        if short and phrase_in(short) and (len(_words(short)) > 1 or len(short) >= 2):
            found.append(Mention(short, (s["id"],)))
    for alias, phrase in SUBJECT_ALIASES.items():
        if alias in req_set:
            ids = tuple(s["id"] for s in subjects if phrase in norm(s["name"]))
            if ids:
                found.append(Mention(alias, ids))
    # 2. the official name, as a phrase in any inflection ("математиката")
    plain = [w for w in req_words if w not in _STOP]
    starts: dict[int, list[dict]] = {}
    for s in subjects:
        name = [w for w in _words(s["name"]) if w not in _STOP]
        n = len(name)
        stems = [w[: max(4, len(w) - 3)] if len(w) >= 4 else w for w in name]
        for i in range(len(plain) - n + 1):
            if n and all(
                plain[i + k].startswith(st)
                and (len(name[k]) >= 4 or plain[i + k] == st)
                for k, st in enumerate(stems)
            ):
                starts.setdefault(i, []).append(s)
    for i, hits in sorted(starts.items()):
        # a longer official name that matches here beats the shorter one inside it
        longest = max(len(_words(s["name"])) for s in hits)
        top = [s for s in hits if len(_words(s["name"])) == longest]
        found.append(Mention(plain[i], tuple(s["id"] for s in top)))
    seen: set[tuple[str, tuple[int, ...]]] = set()
    return [
        m for m in found if not ((m.text, m.ids) in seen or seen.add((m.text, m.ids)))
    ]


# --------------------------------------------------------------------------
# Clock times, ordinal periods and days -> hints
# --------------------------------------------------------------------------

_TIME = (
    r"(?P<kind>след|преди|до|от|не по-късно от|не по-рано от|не по-късно до)\s+"
    r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2})|\s*ч\b\.?)"
)
_NUM_WORDS = {"два": 2, "две": 2, "три": 3, "четири": 4, "пет": 5, "шест": 6}
_NTH = {
    "първия": 1,
    "първият": 1,
    "втория": 2,
    "вторият": 2,
    "третия": 3,
    "третият": 3,
    "четвъртия": 4,
    "четвъртият": 4,
    "петия": 5,
    "петият": 5,
    "шестия": 6,
    "шестият": 6,
}


def _hm(t) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def period_hints(text: str, periods: list[dict]) -> list[str]:
    """Hints like „след 12:00“ -> periods that start at or after 12:00.

    ASSUMPTION: "след/от T" = periods starting at or after T; "преди/до T" = periods
    that end at or before T. A period that straddles T is excluded; say so in the hint.
    ``periods``: dicts with number, start, end (datetime.time), sorted by number.
    """
    out: list[str] = []
    t = norm(text)
    for m in re.finditer(_TIME, t):
        h, mi = int(m.group("h")), int(m.group("m") or 0)
        if h > 23 or mi > 59:
            continue
        limit = time(h, mi)
        if m.group("kind") in ("след", "от", "не по-рано от"):
            nums = [p["number"] for p in periods if p["start"] >= limit]
            rule = f"начало не преди {_hm(limit)}"
        else:
            nums = [p["number"] for p in periods if p["end"] <= limit]
            rule = f"край не след {_hm(limit)}"
        straddle = [p["number"] for p in periods if p["start"] < limit < p["end"]]
        extra = (
            f"; час {straddle[0]} започва преди и свършва след това време, затова е изключен"
            if straddle
            else ""
        )
        out.append(
            f"„{m.group(0)}“ = часове {_range(nums)} ({rule}{extra})"
            if nums
            else f"„{m.group(0)}“ = няма такива часове ({rule}{extra})"
        )
    for m in re.finditer(r"първите\s+(\w+)\s+час\w*", t):
        n = _NUM_WORDS.get(m.group(1)) or (
            int(m.group(1)) if m.group(1).isdigit() else 0
        )
        if n:
            out.append(f"„{m.group(0)}“ = часове {_range(list(range(1, n + 1)))}")
    for m in re.finditer(
        r"\b(първия|първият|втория|вторият|третия|третият|четвъртия|четвъртият|петия|петият|шестия|шестият)\s+час",
        t,
    ):
        out.append(f"„{m.group(0)}“ = час {_NTH[m.group(1)]}")
    return out


def _range(nums: list[int]) -> str:
    if not nums:
        return ""
    runs, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n != prev + 1:
            runs.append((start, prev))
            start = n
        prev = n
    runs.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def day_of(word: str) -> int | None:
    w = norm(word)
    # "пет" alone is the number five, not Friday
    return next((d for stem, d in _DAY_STEMS if w.startswith(stem)), None)


def day_hints(text: str, school_days: int) -> list[str]:
    """Hints for "всеки ден освен петък", "от понеделник до сряда", "всеки ден"."""
    t = norm(text)
    all_days = list(range(1, school_days + 1))
    out: list[str] = []
    word = r"([а-я]+)"
    for m in re.finditer(rf"всеки\s+ден\s+(?:освен|без)\s+{word}(?:\s+и\s+{word})?", t):
        skip = {d for d in map(day_of, filter(None, m.groups())) if d}
        if skip:
            out.append(
                f"„{m.group(0)}“ = дни {_range([d for d in all_days if d not in skip])}"
            )
    for m in re.finditer(rf"от\s+{word}\s+до\s+{word}", t):
        a, b = day_of(m.group(1)), day_of(m.group(2))
        if a and b and a <= b <= school_days:
            out.append(f"„{m.group(0)}“ = дни {_range(list(range(a, b + 1)))}")
    if re.search(r"\bвсеки\s+ден\b", t) and not any("всеки ден" in h for h in out):
        out.append(f"„всеки ден“ = дни {_range(all_days)}")
    return out


# --------------------------------------------------------------------------
# One call that builds everything the prompt needs
# --------------------------------------------------------------------------


@dataclass
class Resolved:
    teachers: list[Mention] = field(default_factory=list)
    classes: list[int] = field(default_factory=list)
    grades: list[int] = field(default_factory=list)
    subjects: list[Mention] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


def resolve(text: str, school: dict) -> Resolved:
    """``school``: plain dict with teachers, classes, subjects, periods, school_days."""
    r = Resolved()
    r.teachers = find_teachers(text, school["teachers"])
    r.classes, r.grades = find_classes(text, school["classes"])
    r.subjects = find_subjects(text, school["subjects"])
    by_id = {t["id"]: t for t in school["teachers"]}
    cls = {c["id"]: c for c in school["classes"]}
    sub = {s["id"]: s for s in school["subjects"]}
    for m in r.teachers:
        names = ", ".join(f"{i} {by_id[i]['name']}" for i in m.ids)
        tag = "" if len(m.ids) == 1 else " (НЕЯСНО: няколко съвпадения, не отгатвайте)"
        r.hints.append(f"учител „{m.text}“ = {names}{tag}")
    for cid in r.classes:
        r.hints.append(f"клас = {cid} {cls[cid]['name']}")
    for g in r.grades:
        r.hints.append(f"випуск {g} (scope grade {g})")
    for m in r.subjects:
        names = ", ".join(f"{i} {sub[i]['name']}" for i in m.ids)
        tag = "" if len(m.ids) == 1 else " (НЕЯСНО: няколко съвпадения)"
        r.hints.append(f"предмет „{m.text}“ = {names}{tag}")
    r.hints += period_hints(text, school["periods"])
    r.hints += day_hints(text, school["school_days"])
    return r
