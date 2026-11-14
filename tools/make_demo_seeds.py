from __future__ import annotations

import json
from pathlib import Path

from tools.asc_import.anonymize import generic_bell_times

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
DAYS = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък"]

TEACHER_NAMES = [
    "Иван Иванов",
    "Петър Петров",
    "Мария Маринова",
    "Георги Георгиев",
    "Николай Николов",
    "Елена Еленова",
    "Димитър Димитров",
    "Светлана Светлова",
    "Александър Александров",
    "Виктор Викторов",
    "Борис Борисов",
    "Васил Василев",
    "Йордан Йорданов",
    "Калина Калинова",
    "Любомир Любенов",
    "Милена Миленова",
    "Огнян Огнянов",
    "Пламен Пламенов",
    "Радослав Радев",
    "Симеон Симеонов",
    "Тихомир Тихов",
    "Цветан Цветанов",
    "Юлиан Юлиев",
    "Яна Янева",
    "Добромир Добрев",
]
ASSUMPTIONS = []
SPLIT = "split"


def make_rooms(regular, labs, gyms):
    kinds = [
        ("R", "Стая", "regular", regular),
        ("L", "Лаборатория", "lab", labs),
        ("G", "Физкултурен салон", "gym", gyms),
    ]
    return [
        {"id": f"{prefix}{n:02d}", "name": f"{name} {n}", "type": rtype}
        for prefix, name, rtype, count in kinds
        for n in range(1, count + 1)
    ]


def lesson(class_id, subject_id, teacher, blocks, group=None):
    return {
        "classes": [{"class": class_id, "group": group}],
        "subject": subject_id,
        "teacher": f"T{teacher:02d}",
        "periods_per_week": sum(blocks),
        "blocks": blocks,
    }


def make_school(description, periods, class_names, plan, rooms):
    classes = [
        {
            "id": f"C{i:02d}",
            "name": name,
            "grade": int(name[:-1]),
            "profile": None,
            "class_teacher": None,
        }
        for i, name in enumerate(class_names, 1)
    ]
    names = list(dict.fromkeys(row[0] for row in plan)) + ["Час на класа"]
    subjects = [{"id": f"S{i:02d}", "name": n} for i, n in enumerate(names, 1)]
    subject_id = {s["name"]: s["id"] for s in subjects}

    curriculum = []
    for name, blocks, teachers, grades, *split in plan:
        mine = [c for c in classes if grades is None or c["grade"] in grades]
        for n, c in enumerate(mine):
            cid, s = c["id"], subject_id[name]
            if split:
                curriculum.append(lesson(cid, s, teachers[0], blocks, "Първа група"))
                curriculum.append(lesson(cid, s, teachers[1], blocks, "Втора група"))
            else:
                teacher = teachers[n * len(teachers) // len(mine)]
                curriculum.append(lesson(cid, s, teacher, blocks))

    teacher_ids = sorted({x["teacher"] for x in curriculum})
    for c, t in zip(classes, teacher_ids, strict=False):
        c["class_teacher"] = t
        curriculum.append(lesson(c["id"], subject_id["Час на класа"], int(t[1:]), [1]))

    return {
        "meta": {
            "description": description,
            "days": DAYS,
            "periods": generic_bell_times(list(range(1, periods + 1))),
            "assumptions": ASSUMPTIONS,
            "known_quirks": [],
            "anonymized": True,
        },
        "teachers": [
            {"id": t, "name": TEACHER_NAMES[int(t[1:]) - 1]} for t in teacher_ids
        ],
        "classes": classes,
        "subjects": subjects,
        "rooms": rooms,
        "curriculum": curriculum,
        "reference_timetable": [],
    }


def tiny_school():
    plan = [
        ("Български език и литература", [1, 1, 1], [1], None),
        ("Математика", [1, 1, 1], [2], None),
        ("Английски език", [1, 1], [3], None),
        ("Физическо възпитание и спорт", [1, 1], [5], None),
        ("Информационни технологии", [2], [6, 7], None, SPLIT),
        ("Програмиране", [2], [8, 9], None, SPLIT),
    ]
    return make_school(
        "Tiny made-up school for fast tests: 3 classes, 5 days x 5 periods.",
        5,
        ["8А", "8Б", "8В"],
        plan,
        make_rooms(regular=3, labs=1, gyms=1),
    )


def demo_school():
    plan = [
        ("Български език и литература", [1, 1, 1, 1], [1, 2, 3], None),
        ("Математика", [1, 1, 1, 1], [4, 5, 6], None),
        ("Английски език", [1, 1, 1, 1], [7, 8, 9], None),
        ("Немски език", [1, 1], [10], {8, 9}),
        ("История и цивилизации", [1, 1], [11], None),
        ("География и икономика", [1, 1], [12], {8, 9, 10}),
        ("Биология и здравно образование", [1, 1], [13], {8, 9, 10}),
        ("Физика и астрономия", [1, 1], [14], {8, 9, 10}),
        ("Химия и опазване на околната среда", [1, 1], [15], {8, 9, 10}),
        ("Философия", [1, 1], [16], {10, 11}),
        ("Физическо възпитание и спорт", [1, 1], [17, 18], None),
        ("Увод в програмирането", [2, 1], [19], {8}),
        ("Информационни технологии", [2], [20, 21], {8}, SPLIT),
        ("Програмиране", [2, 2], [19], {9}),
        ("Операционни системи", [2], [25], {9}),
        ("Обектно-ориентирано програмиране", [2, 2], [22], {10}),
        ("Компютърни мрежи", [2], [20, 21], {10}, SPLIT),
        ("Бази данни", [2], [23, 24], {11}, SPLIT),
        ("Софтуерно инженерство", [2], [25], {11}),
        ("Интернет програмиране", [2, 1], [22], {11}),
    ]
    return make_school(
        "Made-up demo school: 12 classes, 25 teachers, 5 days x 7 periods.",
        7,
        [f"{grade}{letter}" for grade in (8, 9, 10, 11) for letter in "АБВ"],
        plan,
        make_rooms(regular=12, labs=2, gyms=1),
    )


def impossible_school():
    plan = [
        ("Математика", [7, 7, 7], [1], None),
        ("Български език и литература", [1, 1], [2], None),
    ]
    seed = make_school(
        "Deliberately impossible: teacher T01 has 6 full-day blocks in a 5-day week.",
        7,
        ["8А", "8Б"],
        plan,
        make_rooms(regular=2, labs=0, gyms=0),
    )
    seed["meta"]["expected_status"] = "INFEASIBLE"
    return seed


SCHOOLS = {
    "tiny_school.json": tiny_school,
    "demo_school.json": demo_school,
    "impossible_school.json": impossible_school,
}

if __name__ == "__main__":
    for filename, build in SCHOOLS.items():
        text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
        (SEEDS / filename).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote seeds/{filename}")
