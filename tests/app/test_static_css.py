"""A stray brace in app.css silently disables every rule after it, and no page test notices."""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "app" / "static" / "css" / "app.css"


def test_app_css_braces_are_balanced():
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.DOTALL)
    depth = 0
    for ch in text:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        assert depth >= 0, "a '}' closes nothing"
    assert depth == 0, "a '{' is never closed"
