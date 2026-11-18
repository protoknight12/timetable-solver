"""BE-15: standard class names (8А, 12Г) and the school's internal class codes (234).

Plain helpers, no Flask. Users only ever see and type `<grade><letter>`.
"""

import re
from datetime import date

# Й is never used for classes. Parallel 1..10 -> letter.
LETTERS = "АБВГДЕЖЗИК"
NAME_RE = re.compile(r"(1[0-2]|[1-9])([А-Я])")
NAME_ERROR = "Името на класа трябва да е във вида 8А или 12Г."
_LATIN_TO_CYRILLIC = str.maketrans("ABCEHKMOPTX", "АВСЕНКМОРТХ")


def current_school_year_start(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


def normalize_class_name(text: str | None) -> str | None:
    """ "10 г" -> "10Г", Latin "10a" -> "10А". Does not validate.

    None (an empty "new class" form, before anything is typed) stays None.
    """
    if text is None:
        return None
    return "".join(text.split()).upper().translate(_LATIN_TO_CYRILLIC)


def class_name_from_code(code: str, school_year_start: int) -> tuple[int, str]:
    """aSc code -> (grade, name), e.g. ("234", 2025) -> (10, "10Г").

    ASSUMPTION (unconfirmed by the teacher): a code 2YP means "enrolled in 202Y,
    parallel P"; 8th grade is the first year, so grade = 8 + (school_year_start - 202Y)
    and P=1..10 maps to the letters А Б В Г Д Е Ж З И К. Raises ValueError (Bulgarian
    message) for anything else instead of guessing.
    """
    code = code.strip()
    if not re.fullmatch(r"2\d\d", code):
        raise ValueError(
            f"Кодът на класа „{code}“ трябва да е от три цифри (например 234)."
        )
    grade = 8 + school_year_start - (2020 + int(code[1]))
    parallel = int(code[2])
    if not 8 <= grade <= 12:
        raise ValueError(
            f"Кодът „{code}“ не съответства на 8.–12. клас за учебната "
            f"{school_year_start}/{school_year_start + 1} година."
        )
    if not 1 <= parallel <= len(LETTERS):
        raise ValueError(f"Кодът „{code}“ има невалиден паралелен клас ({parallel}).")
    return grade, f"{grade}{LETTERS[parallel - 1]}"
