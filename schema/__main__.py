"""Regenerate schema/constraint.schema.json: ``uv run python -m schema``."""

from schema.constraints import SCHEMA_FILE, _render_schema

SCHEMA_FILE.write_text(_render_schema(), encoding="utf-8", newline="\n")
print(f"wrote {SCHEMA_FILE}")
