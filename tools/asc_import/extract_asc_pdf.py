"""Extract lessons from an aSc Timetables "class timetable" PDF export.

Raw extraction only: returns real names. Anonymization is a separate step
(anonymize.py) so the raw output never needs to be written to the repo.

Layout assumptions (verified on one aSc export, Nov 2026):
- one page per class; class code is the only text larger than 30pt
- each lesson is a filled, coloured rectangle
- inside a box: tiny Arial text = group label, bold text in the largest size
  = subject, other bold text = room, regular Times text = teacher
- period rows are labelled 1..N in the left margin (x < 88pt)
"""

import json
import re
import sys

import pdfplumber

DAYS = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък"]


def _is_box(r):
    c = r["non_stroking_color"]
    if not r["fill"] or r["bottom"] - r["top"] < 5:
        return False
    if isinstance(c, (int, float)):
        return c not in (0, 1)  # grey (e.g. PE) counts, black/white don't
    return tuple(c) not in ((0,), (1,), (0, 0, 0), (1, 1, 1))


def _center(c):
    return (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2


def _lines(chars, tol=3):
    """Group chars into text lines (top-to-bottom), each line left-to-right."""
    out = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        if out and abs(out[-1][0]["top"] - c["top"]) <= tol:
            out[-1].append(c)
        else:
            out.append([c])
    return [
        "".join(ch["text"] for ch in sorted(l, key=lambda c: c["x0"])).strip()
        for l in out
    ]


def parse_page(page):
    words = page.extract_words()
    class_code = "".join(c["text"] for c in page.chars if c["size"] > 30).strip()
    m = re.search(r"Класен\s*:\s*(.+)", page.extract_text() or "")
    class_teacher = m.group(1).strip() if m else None

    day_x = {}
    for w in words:
        if w["text"] in DAYS and w["top"] < 110:
            day_x[w["text"]] = (w["x0"] + w["x1"]) / 2
    day_bounds = sorted(day_x.items(), key=lambda kv: kv[1])
    width = (day_bounds[-1][1] - day_bounds[0][1]) / (len(day_bounds) - 1)
    col_left = [(d, x - width / 2) for d, x in day_bounds]

    periods = {}  # period number -> y center of its label
    for w in words:
        if (
            w["x0"] < 88
            and w["text"].isdigit()
            and w["top"] > 100
            and w["bottom"] < 580
        ):
            periods[int(w["text"])] = (w["top"] + w["bottom"]) / 2

    boxes = [r for r in page.rects if _is_box(r)]
    # Text in narrow boxes overflows into neighbouring boxes (left-aligned
    # names spill right, centred subjects spill both ways), so assign each
    # contiguous run of chars to the box containing the run's centre.
    runs = []
    for c in sorted(
        page.chars, key=lambda c: (round(c["top"]), c["fontname"], c["size"], c["x0"])
    ):
        r = runs[-1] if runs else None
        if (
            r
            and abs(r[-1]["top"] - c["top"]) < 0.5
            and r[-1]["fontname"] == c["fontname"]
            and abs(r[-1]["size"] - c["size"]) < 0.1
            and -0.5 <= c["x0"] - r[-1]["x1"] < 0.3
        ):
            r.append(c)
        else:
            runs.append([c])
    owner = {}
    for r in runs:
        x = (r[0]["x0"] + r[-1]["x1"]) / 2
        y = _center(r[0])[1]
        for i, b in enumerate(boxes):
            if b["x0"] - 1 <= x <= b["x1"] + 1 and b["top"] - 1 <= y <= b["bottom"] + 1:
                for c in r:
                    owner[id(c)] = i
                break
    lessons = []
    for bi, b in enumerate(boxes):
        # day = last column whose left edge is at or before the box's left side
        day = [d for d, left in col_left if left <= b["x0"] + 3][-1]
        ps = sorted(p for p, y in periods.items() if b["top"] < y < b["bottom"])
        chars = [c for c in page.chars if owner.get(id(c)) == bi]
        group = [c for c in chars if "Arial" in c["fontname"] and c["size"] < 7]
        bold = [c for c in chars if "Bold" in c["fontname"]]
        regular = [
            c
            for c in chars
            if "Bold" not in c["fontname"] and "Arial" not in c["fontname"]
        ]
        subj_size = max((c["size"] for c in bold), default=0)
        subject = [c for c in bold if abs(c["size"] - subj_size) < 0.3]
        room = [c for c in bold if abs(c["size"] - subj_size) >= 0.3]
        lessons.append(
            {
                "class": class_code,
                "day": DAYS.index(day),
                "periods": ps,
                "subject_raw": " ".join(_lines(subject)),
                "teacher_raw": "".join(_lines(regular)),
                "room_raw": "".join(_lines(room)),
                "group_raw": " ".join(_lines(group)) or None,
                "box": [
                    round(b["x0"]),
                    round(b["top"]),
                    round(b["x1"]),
                    round(b["bottom"]),
                ],
            }
        )
    times = [
        w["text"]
        for w in sorted(words, key=lambda w: w["top"])
        if w["x0"] < 88 and re.fullmatch(r"\d{1,2}:\d{2}", w["text"])
    ]
    period_times = {
        p: [times[2 * i], times[2 * i + 1]]
        for i, p in enumerate(sorted(periods))
        if 2 * i + 1 < len(times)
    }
    return {
        "class": class_code,
        "class_teacher": class_teacher,
        "lessons": lessons,
        "period_times": period_times,
    }


def main(pdf_path, out_path):
    with pdfplumber.open(pdf_path) as pdf:
        pages = [parse_page(p) for p in pdf.pages]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
