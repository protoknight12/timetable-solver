"""BE-21: the deterministic text helpers around the LLM (no Flask, no network)."""

from datetime import time

import pytest

from app import llm_resolve as r

TEACHERS = [
    {"id": 1, "name": "Иван Иванов"},
    {"id": 2, "name": "Мария Иванова"},
    {"id": 3, "name": "Петър Петров"},
    {"id": 4, "name": "Елена Стоянова"},
]
CLASSES = [
    {"id": 10, "name": "8А"},
    {"id": 11, "name": "11 А"},
    {"id": 12, "name": "11Б (профил)"},
    {"id": 13, "name": "12А"},
]
SUBJECTS = [
    {"id": 1, "name": "Математика", "short_name": "Мат."},
    {"id": 2, "name": "Математика ФУЧ", "short_name": None},
    {"id": 3, "name": "Физическо възпитание и спорт", "short_name": "ФВС"},
    {"id": 4, "name": "Час на класа", "short_name": None},
    {"id": 5, "name": "Български език и литература", "short_name": "БЕЛ"},
    {"id": 6, "name": "Информационни технологии", "short_name": "ИТ"},
]
PERIODS = [
    {"number": 1, "start": time(7, 30), "end": time(8, 15)},
    {"number": 2, "start": time(8, 25), "end": time(9, 10)},
    {"number": 3, "start": time(9, 20), "end": time(10, 5)},
    {"number": 4, "start": time(10, 25), "end": time(11, 10)},
    {"number": 5, "start": time(11, 20), "end": time(12, 5)},
    {"number": 6, "start": time(12, 15), "end": time(13, 0)},
    {"number": 7, "start": time(13, 10), "end": time(13, 55)},
]
SCHOOL = {
    "teachers": TEACHERS,
    "classes": CLASSES,
    "subjects": SUBJECTS,
    "periods": PERIODS,
    "school_days": 5,
}


@pytest.mark.parametrize(
    "word",
    ["Иванов", "Иванова", "Ивановия", "Ивановите", "иванова", "Ивановата"],
)
def test_surname_key_ignores_inflection(word):
    assert r.surname_key(word) == "иванов"


def test_surname_key_leaves_other_names_alone():
    assert r.surname_key("Стоянова") == "стоянов"
    assert r.surname_key("Петровски") == r.surname_key("Петровска") == "петровск"


def ids(mentions):
    return [m.ids for m in mentions]


def test_teacher_found_in_inflected_form():
    assert ids(r.find_teachers("Часовете на Петров в петък", TEACHERS)) == [(3,)]
    assert ids(r.find_teachers("Стоянова не може", TEACHERS)) == [(4,)]


def test_shared_surname_is_ambiguous_until_a_name_or_initial_narrows_it():
    assert ids(r.find_teachers("Иванов не може в петък", TEACHERS)) == [(1, 2)]
    assert ids(r.find_teachers("Мария Иванова не може", TEACHERS)) == [(2,)]
    assert ids(r.find_teachers("И. Иванов не може", TEACHERS)) == [(1,)]
    assert ids(r.find_teachers("М. Иванова не може", TEACHERS)) == [(2,)]
    assert ids(r.find_teachers("Ив. Иванов не може", TEACHERS)) == [(1,)]


def test_teacher_verdict():
    found = r.find_teachers("Иванов и Петров", TEACHERS)
    assert r.teacher_verdict(3, found) is None
    assert "няколко учители" in r.teacher_verdict(1, found)
    assert "не е назован" in r.teacher_verdict(4, found)


@pytest.mark.parametrize(
    "text",
    [
        "11а",
        "11 А",
        "11-а",
        "11a",
        "единайсети а",
        "Единадесети А",
        "XI а",
        "11 а клас",
    ],
)
def test_class_spellings_resolve_to_the_same_class(text):
    assert r.find_classes(f"Без часове за {text} в петък", CLASSES) == ([11], [])


def test_class_name_with_suffix_and_two_classes():
    assert r.find_classes("11 Б и осми а", CLASSES)[0] == [12, 10]


def test_digit_and_letter_only_count_when_the_class_exists():
    # "2 и 3 часа" must not turn into class "2и"
    assert r.find_classes("първите 2 и 3 часа и 5 а", CLASSES) == ([], [])


def test_whole_grades():
    assert r.find_classes("всички единайсети класове", CLASSES) == ([], [11])
    assert r.find_classes("за 12 клас", CLASSES) == ([], [12])
    assert r.find_classes("за 7 клас", CLASSES) == ([], [])  # no such grade


def test_subjects_short_names_aliases_and_inflection():
    def names(text):
        return ids(r.find_subjects(text, SUBJECTS))

    assert (3,) in names("ФВС не в първия час")
    assert (3,) in names("физкултура само сутрин")
    assert (5,) in names("БЕЛ след обяд")
    assert (1,) in names("математиката не повече от два часа")
    assert (6,) in names("ИТ в лаборатория")


def test_subject_stem_match_needs_the_whole_name_in_order():
    # "час" and "класове" appear, but not as the subject "Час на класа"
    assert r.find_subjects("часове за всички класове", SUBJECTS) == []
    assert ids(r.find_subjects("Час на класа в петък", SUBJECTS)) == [(4,)]


def test_period_hint_after_clock_time_names_the_straddling_period():
    (hint,) = r.period_hints("не може след 12:00", PERIODS)
    assert "часове 6–7" in hint and "час 5 започва преди" in hint


def test_period_hint_before_time_and_numeric_hour_marker():
    (a,) = r.period_hints("не започва преди 9:20", PERIODS)
    assert "часове 1–2" in a
    (b,) = r.period_hints("до 11 ч. свободен", PERIODS)
    assert "часове 1–3" in b  # periods ending by 11:00


def test_hour_counts_are_not_clock_times():
    assert r.period_hints("не повече от 3 часа на ден, след 5 часа", PERIODS) == []


def test_first_n_periods():
    assert "часове 1–2" in r.period_hints("първите два часа", PERIODS)[0]
    assert "часове 1–3" in r.period_hints("първите 3 часа", PERIODS)[0]
    assert r.period_hints("не в първия час", PERIODS)[0].endswith("час 1")
    assert r.period_hints("във втория час", PERIODS)[0].endswith("час 2")


def test_day_hints():
    assert "дни 1–4" in r.day_hints("всеки ден освен петък", 5)[0]
    assert "дни 1, 4–5" in r.day_hints("всеки ден без вторник и сряда", 5)[0]
    assert "дни 1–3" in r.day_hints("от понеделник до сряда", 5)[0]
    assert "дни 1–5" in r.day_hints("всеки ден", 5)[0]
    assert r.day_hints("пет часа в петък", 5) == []


def test_day_of_does_not_take_the_number_five():
    assert r.day_of("петък") == 5 and r.day_of("петъците") == 5
    assert r.day_of("пет") is None and r.day_of("срядата") == 3


def test_resolve_collects_hints_for_the_prompt():
    found = r.resolve("Иванов и Стоянова не могат след 12:00, 11а първия час", SCHOOL)
    text = "\n".join(found.hints)
    assert "НЕЯСНО" in text  # Иванов
    assert "4 Елена Стоянова" in text
    assert "клас = 11 11 А" in text
    assert "часове 6–7" in text
    assert found.classes == [11]
