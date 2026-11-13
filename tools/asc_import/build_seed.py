"""Turn raw aSc extraction output into an anonymized seed dataset.

Usage: python build_seed.py raw.json seeds/example_school.json

Input is the output of extract_asc_pdf.py (contains real names, never commit
it). Output contains no real person names and no real class names: teachers
get generated names, classes get generic grade/letter names. The real->fake
mapping is held in memory only and is never written anywhere.

Subject names and the timetable structure are kept: they are what makes the
dataset realistic. Room codes, bell times and the real days are then hidden by
the second pass in ``anonymize.py``, which runs automatically below.
"""

import json
import random
import re
import sys
from collections import Counter, defaultdict

from anonymize import anonymize  # same folder; run this file as a script

DAY_NAMES = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък"]

# Narrow aSc boxes wrap words mid-way ("Испанс ки"). Matching subjects by
# their text with all spaces removed folds those variants together; this
# table picks the display form for keys that only ever appear wrapped.
SUBJECT_DISPLAY = {
    "Испански": "Испански",
    "Немски": "Немски",
    "Китайски": "Китайски",
    "МПТ-УП": "МПТ - УП",
    "Ув.прог-УП": "Ув.прог - УП",
    "ПТД-ФУЧ": "ПТД - ФУЧ",
}
# Two cells where neighbouring narrow boxes overlap so much that text from
# one lands in the other. Checked by eye against the rendered PDF page.
SUBJECT_FIXES = {"УА СД -СД - УП": "УА СД - УП", "УА УП": "УА СД - УП"}

TITLES = ("проф.", "доц.", "д-р")

FIRST_F = [
    "Анелия",
    "Борислава",
    "Весела",
    "Дарина",
    "Елица",
    "Жана",
    "Зорница",
    "Ивета",
    "Калина",
    "Лиляна",
    "Милена",
    "Невена",
    "Ралица",
    "Силвия",
    "Теодора",
    "Христина",
    "Цветелина",
    "Яна",
    "Росица",
    "Камелия",
    "Десислава",
    "Мирослава",
    "Красимира",
    "Венета",
    "Антоанета",
    "Боряна",
    "Емилия",
    "Капка",
    "Люба",
    "Снежана",
    "Таня",
]
FIRST_M = [
    "Асен",
    "Борислав",
    "Веселин",
    "Георги",
    "Димо",
    "Емил",
    "Живко",
    "Златко",
    "Иво",
    "Калоян",
    "Любомир",
    "Митко",
    "Огнян",
    "Пламен",
    "Румен",
    "Светослав",
    "Тодор",
    "Христо",
    "Чавдар",
    "Ангел",
    "Велизар",
    "Кирил",
    "Росен",
    "Здравко",
    "Методи",
]
SURNAME_BASES = [
    "Арнаудов",
    "Бонев",
    "Вутов",
    "Гърков",
    "Дечев",
    "Енчев",
    "Жеков",
    "Зарев",
    "Илчев",
    "Каменов",
    "Лазов",
    "Марков",
    "Начков",
    "Огнянов",
    "Пасков",
    "Ралчев",
    "Сарафов",
    "Тасев",
    "Узунов",
    "Филев",
    "Хаджиев",
    "Цонев",
    "Чолаков",
    "Шопов",
    "Янков",
    "Алексиев",
    "Бъчваров",
    "Влахов",
    "Гатев",
    "Дойнов",
    "Ефтимов",
    "Златанов",
    "Икономов",
    "Караиванов",
    "Лулчев",
    "Мутафов",
    "Ненчев",
    "Обретенов",
    "Пеняков",
    "Русинов",
    "Стамболов",
    "Тошков",
    "Урумов",
    "Фотев",
    "Хинков",
    "Цанков",
    "Чапанов",
    "Шишков",
    "Ямболиев",
    "Анастасов",
    "Белчев",
    "Велков",
    "Гюров",
    "Дрянов",
    "Есков",
    "Жилков",
    "Захов",
    "Игнатов",
    "Колев",
    "Лилов",
    "Минчев",
    "Нанков",
    "Орешков",
]


def norm_key(s):
    return re.sub(r"[\s.]", "", s or "").lower()


def subject_key(s):
    return re.sub(r"\s", "", s)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def is_female(name):
    tokens = [
        t for t in re.split(r"[\s.]+", name) if t and t not in ("проф", "доц", "д-р")
    ]
    longest = [t for t in tokens if len(t) > 2]
    word = longest[-1] if longest else tokens[-1]
    return word.endswith("а")


def build(pages, rng_seed=20261204):
    # ---- subjects ---------------------------------------------------------
    subj_variants = defaultdict(Counter)
    for p in pages:
        for le in p["lessons"]:
            raw = SUBJECT_FIXES.get(le["subject_raw"], le["subject_raw"])
            le["subject_raw"] = raw
            subj_variants[subject_key(raw)][clean(raw)] += 1
    subj_name = {}
    for k, variants in subj_variants.items():
        subj_name[k] = SUBJECT_DISPLAY.get(k) or variants.most_common(1)[0][0]

    # ---- teachers (real names, in memory only) ---------------------------
    t_variants = defaultdict(Counter)
    for p in pages:
        for le in p["lessons"]:
            t_variants[norm_key(le["teacher_raw"])][clean(le["teacher_raw"])] += 1
    real_name = {k: v.most_common(1)[0][0] for k, v in t_variants.items()}

    # ---- class teachers: header full name -> cell short name -------------
    def match_class_teacher(full, page):
        if full:
            parts = full.split()
            first, last = parts[0], parts[-1]
            for k, nm in real_name.items():
                toks = [t for t in re.split(r"[\s.]+", nm) if t]
                if (
                    toks
                    and toks[-1] == last
                    and first.startswith(toks[0][:2].rstrip("."))
                ):
                    return k
        # Fallback: the teacher of "Час на класа" (form period) for this class.
        for le in page["lessons"]:
            if subject_key(le["subject_raw"]) == "Часнакласа":
                return norm_key(le["teacher_raw"])
        return None

    # ---- anonymize ---------------------------------------------------------
    rng = random.Random(rng_seed)
    real_surnames = {
        re.sub(r"а$", "", w)
        for nm in real_name.values()
        for w in re.split(r"[\s.]+", nm)
        if len(w) > 2
    }
    bases = [b for b in SURNAME_BASES if b not in real_surnames]
    rng.shuffle(bases)
    ff, fm = FIRST_F[:], FIRST_M[:]
    rng.shuffle(ff)
    rng.shuffle(fm)
    teacher_ids, teachers = {}, []
    for i, k in enumerate(
        sorted(real_name, key=lambda k: -sum(t_variants[k].values()))
    ):
        tid = f"T{i + 1:02d}"
        teacher_ids[k] = tid
        nm = real_name[k]
        if nm.startswith("Нов "):
            # aSc placeholder for a not-yet-hired teacher; keep it as one.
            teachers.append({"id": tid, "name": "Незает щат", "placeholder": True})
            continue
        title = next((t for t in TITLES if nm.startswith(t)), None)
        fem = is_female(nm)
        base = bases.pop()
        first = (ff if fem else fm).pop()
        fake = f"{first} {base}{'а' if fem else ''}"
        teachers.append({"id": tid, "name": f"{title} {fake}" if title else fake})

    # ---- classes -----------------------------------------------------------
    # ASSUMPTION: codes 22x..26x are five cohorts mapped to grades 8..12 and the
    # last digit is the parallel class (1=А, 2=Б, ...). The PDF does not say so.
    letters = "АБВГДЕЖЗ"
    class_ids, classes = {}, []
    for p in pages:
        code = p["class"]
        m = re.fullmatch(r"2(\d)(\d)(?:-(.+))?", code)
        grade = int(m.group(1)) + 6
        name = f"{grade}{letters[int(m.group(2)) - 1]}"
        cid = f"C{len(classes) + 1:02d}"
        class_ids[code] = cid
        ct = match_class_teacher(p["class_teacher"], p)
        classes.append(
            {
                "id": cid,
                "name": name,
                "grade": grade,
                "profile": m.group(3),
                "class_teacher": teacher_ids.get(ct),
            }
        )

    # ---- subjects & rooms --------------------------------------------------
    subj_ids = {k: f"S{i + 1:02d}" for i, k in enumerate(sorted(subj_name))}
    subjects = [{"id": subj_ids[k], "name": subj_name[k]} for k in sorted(subj_name)]
    room_codes = sorted(
        {le["room_raw"] for p in pages for le in p["lessons"] if le["room_raw"]}
    )

    def room_type(code):
        # ASSUMPTION: "СБ" is the sports hall, "STEMn" are STEM labs.
        if code == "СБ":
            return "gym"
        if code.startswith("STEM"):
            return "lab"
        return "regular"

    rooms = [{"id": r, "name": r, "type": room_type(r)} for r in room_codes]

    # ---- activities: one per (teacher, subject, slot); joint classes merged
    acts = {}
    for p in pages:
        for le in p["lessons"]:
            ps = le["periods"]
            key = (
                norm_key(le["teacher_raw"]),
                subject_key(le["subject_raw"]),
                le["day"],
                ps[0],
                len(ps),
            )
            a = acts.setdefault(
                key,
                {
                    "teacher": teacher_ids[key[0]],
                    "subject": subj_ids[key[1]],
                    "room": None,
                    "day": le["day"],
                    "start_period": ps[0],
                    "length": len(ps),
                    "classes": [],
                },
            )
            a["room"] = a["room"] or le["room_raw"] or None
            entry = {"class": class_ids[p["class"]], "group": le["group_raw"]}
            if entry not in a["classes"]:
                a["classes"].append(entry)
    activities = sorted(
        acts.values(), key=lambda a: (a["day"], a["start_period"], a["teacher"])
    )
    for i, a in enumerate(activities):
        a["classes"].sort(key=lambda c: (c["class"], c["group"] or ""))
        a["id"] = f"A{i + 1:03d}"

    # ---- curriculum: weekly load per (classes+groups, subject, teacher) ----
    cur = defaultdict(list)
    for a in activities:
        k = (
            tuple((c["class"], c["group"]) for c in a["classes"]),
            a["subject"],
            a["teacher"],
        )
        cur[k].append(a["length"])
    curriculum = [
        {
            "classes": [{"class": c, "group": g} for c, g in k[0]],
            "subject": k[1],
            "teacher": k[2],
            "periods_per_week": sum(v),
            "blocks": sorted(v, reverse=True),
        }
        for k, v in sorted(
            cur.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2])
        )
    ]

    period_times = {int(k): v for k, v in pages[0]["period_times"].items()}
    return {
        "meta": {
            "description": "Anonymized from a real aSc timetable export. Teacher and class "
            "names are generated; structure, subjects and rooms are real.",
            "days": DAY_NAMES,
            "periods": [
                {"number": n, "start": t[0], "end": t[1]}
                for n, t in sorted(period_times.items())
            ],
            "assumptions": [
                "Class codes 22x..26x mapped to grades 8..12; last digit = parallel letter.",
                "Room 'СБ' is the sports hall and holds several PE lessons at once.",
                "Rooms 'STEMn' are labs; numbered rooms are regular classrooms.",
                "'Първа група'/'Втора група' and language names are group labels as printed.",
            ],
            # Present in the source timetable; a strict "one lesson per slot" check
            # flags them. How the school really runs them is an open question.
            "known_quirks": [
                (
                    "Some practice (УП) slots list both groups of a class in two different "
                    "lessons at once (e.g. ИТ + Ув.прог); how groups rotate is unknown."
                ),
                "PE teachers have overlapping blocks with different classes in 'СБ'.",
                "Several lessons are joint across classes (languages, PE, some ТП lectures).",
            ],
        },
        "teachers": teachers,
        "classes": classes,
        "subjects": subjects,
        "rooms": rooms,
        "curriculum": curriculum,
        "reference_timetable": activities,
    }


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        data = anonymize(build(json.load(f)))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
