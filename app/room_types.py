"""The fixed list of room types (BE-20): one source of truth for codes and labels.

The solver and the checker only compare the code string for equality, so the
contract does not know this list. The database stores the code; the pages show
the Bulgarian label.
"""

from __future__ import annotations

ROOM_TYPES: tuple[tuple[str, str], ...] = (
    ("classroom", "Класна стая"),
    ("lab", "Лаборатория / STEM кабинет"),
    ("lecture", "Лекционна зала"),
    ("gym", "Физкултурен салон"),
    ("workshop", "Работилница"),
    ("other", "Друго"),
)
LABELS = dict(ROOM_TYPES)
OTHER = "other"

# Older spellings that clearly mean one of the types: "стая" is what the aSc
# import used to write, "regular" is the code of the demo seeds.
_ALIASES = {"стая": "classroom", "regular": "classroom"}
_BY_TEXT = {
    **{code: code for code in LABELS},
    **{label.casefold(): code for code, label in ROOM_TYPES},
    **_ALIASES,
}


def room_type_label(code: str | None) -> str:
    """The Bulgarian label of a code; an unknown code is shown as it is."""
    if not code:
        return "—"
    return LABELS.get(code, code)


def normalize_room_type(value: str | None) -> str | None:
    """A code, a Bulgarian label (any case) or a known older spelling -> the code.

    None when the text is empty or means none of the types.
    """
    text = (value or "").strip().casefold()
    return _BY_TEXT.get(text)


def room_type_choices(blank: str | None = None) -> list[tuple[str, str]]:
    """Choices for a SelectField; ``blank`` adds a first empty option with that label."""
    choices = list(ROOM_TYPES)
    return [("", blank), *choices] if blank is not None else choices
