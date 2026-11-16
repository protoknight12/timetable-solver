"""Subject colors for the timetable (BE-16): palette, validation, contrast, CSS."""

import re

# Used when a subject has no color of its own; picked by subject id.
PALETTE = (
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
    "#86bcb6",
    "#d37295",
)
# The color ends up inside CSS: only this exact form is ever allowed.
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def is_color(value: str | None) -> bool:
    return bool(value) and COLOR_RE.fullmatch(value) is not None


def subject_color(subject) -> str:
    """The stored color, or the automatic one; rows that bypassed the form are re-checked."""
    if is_color(subject.color):
        return subject.color.lower()
    return PALETTE[subject.id % len(PALETTE)]


def text_color(background: str) -> str:
    """Black or white, whichever has the higher WCAG contrast (always >= 4.5:1)."""

    def channel(i: int) -> float:
        c = int(background[1 + 2 * i : 3 + 2 * i], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
    return "#000000" if (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) else "#ffffff"


def subject_css(subjects) -> str:
    """One rule per subject; the page emits it in a single <style> block (no style="")."""
    rules = []
    for s in subjects:
        bg = subject_color(s)
        rules.append(
            f".subject-{int(s.id)}{{background-color:{bg};color:{text_color(bg)}}}"
        )
    return "\n".join(rules)
