"""Reading a form the client already filled in — a Google Forms response export.

The office sends a family a Google Form (or any form that exports a spreadsheet),
downloads the responses as CSV, and drops that file in here. This module turns a
row of that spreadsheet into intake answers.

The hard part is not reading the file, it is knowing which column is which field.
A form asks "What is the child's full legal name?"; the schema calls it
``child1_name``. So every column is matched to a field, the match is *shown to
staff to confirm*, and the confirmed answer is remembered in
``config/form_mapping.json`` so the same form maps itself next time.

Nothing is guessed silently. A column that cannot be matched is reported, not
dropped quietly, and a value that does not fit its field (a date that is not a
date, an option that is not on the list) is left unset with a note saying so.
Half-filled intake is recoverable; a wrong name in a petition is not.
"""

from __future__ import annotations

import csv
import difflib
import json
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.registry import CONFIG_DIR
from app.schema import FieldDef, Schema, parse_date

MAPPING_FILE = "form_mapping.json"

#: Columns a form export always has that are never intake answers.
BOILERPLATE = ("timestamp", "email address", "score", "username", "submission id", "id")

#: How a column got matched, best first. Shown to staff so they know what to check.
CONFIDENCE = ("remembered", "exact", "label", "similar", "unmatched")

_YES = {"yes", "y", "true", "1", "x", "✓", "checked", "on", "yes - please", "affirmative"}
_NO = {"no", "n", "false", "0", "", "unchecked", "off", "none", "n/a", "na"}

_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y/%m/%d",
)

_NOISE = re.compile(
    r"^(please\s+)?(enter|provide|tell us|what is|what's|what are|list)\s+"
    r"(the|your|his|her|their)?\s*", re.I
)
_PUNCT = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# reading the file
# --------------------------------------------------------------------------


@dataclass
class Sheet:
    """A response export: one row per person who filled the form in."""

    path: Path
    headers: list[str]
    rows: list[list[str]]

    def value(self, row_index: int, column_index: int) -> str:
        row = self.rows[row_index]
        return row[column_index].strip() if column_index < len(row) else ""

    def samples(self, column_index: int, limit: int = 3) -> list[str]:
        seen = []
        for index in range(len(self.rows)):
            text = self.value(index, column_index)
            if text and text not in seen:
                seen.append(text)
            if len(seen) >= limit:
                break
        return seen

    def row_labels(self) -> list[str]:
        """Something human to pick a row by, when a form has several responses."""
        labels = []
        for index in range(len(self.rows)):
            parts = [self.value(index, c) for c in range(len(self.headers))]
            filled = [p for p in parts if p]
            labels.append(" · ".join(filled[:3]) if filled else f"Row {index + 1}")
        return labels


def read_sheet(path: Path) -> Sheet:
    """Read a CSV or TSV export. Google writes UTF-8 with a byte-order mark."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"{path} does not exist")

    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="cp1252")
        except UnicodeDecodeError:
            raise ValueError(
                f"{path.name} is not a text file this app can read. Export the responses "
                f"from Google Forms as CSV (File → Download → Comma-separated values)."
            ) from None

    if not text.strip():
        raise ValueError(f"{path.name} is empty")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel  # a single-column file sniffs as nothing; comma is fine

    rows = [row for row in csv.reader(text.splitlines(), dialect) if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"{path.name} has no rows")

    headers = [cell.strip() for cell in rows[0]]
    if not any(headers):
        raise ValueError(f"{path.name} has no column headings in its first row")
    if len(rows) == 1:
        raise ValueError(
            f"{path.name} has column headings but no responses under them — "
            f"it looks like nobody has filled the form in yet."
        )
    return Sheet(path=path, headers=headers, rows=rows[1:])


# --------------------------------------------------------------------------
# matching columns to fields
# --------------------------------------------------------------------------


def normalise(text: str) -> str:
    """'What is the Child's date of birth?' -> 'child date of birth'."""
    lowered = _NOISE.sub("", text.strip().lower())
    lowered = re.sub(r"\(.*?\)", " ", lowered)          # "(optional)", "(mm/dd/yyyy)"
    return _PUNCT.sub(" ", lowered).strip()


@dataclass
class Column:
    index: int
    header: str
    samples: list[str] = dc_field(default_factory=list)
    field_id: str | None = None
    confidence: str = "unmatched"

    @property
    def is_boilerplate(self) -> bool:
        return normalise(self.header) in {normalise(b) for b in BOILERPLATE}

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "header": self.header,
            "samples": self.samples,
            "field_id": self.field_id,
            "confidence": self.confidence,
            "boilerplate": self.is_boilerplate,
        }


def _candidates(schema: Schema, groups: Iterable[str]) -> list[FieldDef]:
    return [fd for fd in schema.input_fields(groups)]


def match_columns(
    sheet: Sheet,
    schema: Schema,
    groups: Sequence[str],
    remembered: dict[str, str | None] | None = None,
) -> list[Column]:
    """Suggest a field for every column, best match first, no duplicates.

    Order of preference: what staff confirmed last time, then the field's own id,
    then its label, then a close-enough label. Two columns are never given the
    same field — the weaker match is left unmatched for a human to sort out,
    because silently overwriting one answer with another is exactly the kind of
    quiet wrong that puts the wrong name in a petition.
    """
    remembered = remembered or {}
    fields = _candidates(schema, groups)
    by_id = {fd.id: fd for fd in fields}
    by_label = {normalise(fd.label): fd for fd in fields}
    by_qualified = {normalise(schema.label_for(fd)): fd for fd in fields}

    columns = [
        Column(index=index, header=header, samples=sheet.samples(index))
        for index, header in enumerate(sheet.headers)
    ]

    #: (confidence rank, column, field id) for every plausible pairing
    proposals: list[tuple[int, Column, str]] = []
    for column in columns:
        if column.is_boilerplate:
            continue
        key = normalise(column.header)

        if column.header in remembered:
            target = remembered[column.header]
            if target is None:
                continue  # staff said to ignore this one
            if target in by_id:
                proposals.append((0, column, target))
                continue

        if key in {normalise(fid) for fid in by_id}:
            hit = next(fid for fid in by_id if normalise(fid) == key)
            proposals.append((1, column, hit))
            continue
        if key in by_label:
            proposals.append((2, column, by_label[key].id))
            continue
        if key in by_qualified:
            proposals.append((2, column, by_qualified[key].id))
            continue

        pool = {**by_label, **by_qualified}
        close = difflib.get_close_matches(key, list(pool), n=1, cutoff=0.75)
        if close:
            proposals.append((3, column, pool[close[0]].id))

    taken: set[str] = set()
    for rank, column, field_id in sorted(proposals, key=lambda p: (p[0], p[1].index)):
        if field_id in taken:
            continue
        column.field_id = field_id
        column.confidence = CONFIDENCE[rank]
        taken.add(field_id)

    return columns


# --------------------------------------------------------------------------
# turning a row into answers
# --------------------------------------------------------------------------


def coerce(raw: str, fd: FieldDef) -> tuple[Any, str | None]:
    """Return (value, problem). A value that does not fit its field is not used."""
    text = raw.strip()
    if not text:
        return None, None

    if fd.type == "bool":
        lowered = text.lower()
        if lowered in _YES:
            return True, None
        if lowered in _NO:
            return False, None
        return None, f"{fd.label}: {text!r} is not a yes or no answer"

    if fd.type == "select":
        key = normalise(text)
        for option in fd.options:
            if normalise(option) == key:
                return option, None
        for option in fd.options:                       # "Relinquished her rights" -> relinquished
            if key.startswith(normalise(option)) or normalise(option).startswith(key):
                return option, None
        return None, f"{fd.label}: {text!r} is not one of {', '.join(fd.options)}"

    if fd.type == "date":
        head = text.split()[0] if " " in text else text  # a Google timestamp carries a time
        for candidate in (text, head):
            for fmt in _DATE_FORMATS:
                try:
                    from datetime import datetime

                    return datetime.strptime(candidate, fmt).date().isoformat(), None
                except ValueError:
                    continue
        try:
            return parse_date(text).isoformat(), None
        except ValueError:
            return None, f"{fd.label}: {text!r} is not a date (use month/day/year)"

    if fd.type == "number":
        cleaned = re.sub(r"[^0-9.\-]", "", text)
        try:
            return float(cleaned) if "." in cleaned else int(cleaned), None
        except ValueError:
            return None, f"{fd.label}: {text!r} is not a number"

    return text, None


def to_values(
    sheet: Sheet,
    row_index: int,
    columns: Sequence[Column],
    schema: Schema,
) -> tuple[dict[str, Any], list[str]]:
    """Build the intake answers for one response, and everything worth saying about it."""
    if not 0 <= row_index < len(sheet.rows):
        raise ValueError(f"there is no response number {row_index + 1} in {sheet.path.name}")

    values: dict[str, Any] = {}
    notes: list[str] = []

    for column in columns:
        if column.field_id is None:
            if not column.is_boilerplate and any(column.samples):
                notes.append(f"Column {column.header!r} was not used — no field is mapped to it.")
            continue

        fd = schema.fields.get(column.field_id)
        if fd is None:
            notes.append(f"Column {column.header!r} is mapped to {column.field_id}, which no longer exists.")
            continue

        value, problem = coerce(sheet.value(row_index, column.index), fd)
        if problem:
            notes.append(f"{problem} — left blank, from column {column.header!r}.")
        elif value is not None:
            values[fd.id] = value

    return values, notes


# --------------------------------------------------------------------------
# remembering the mapping
# --------------------------------------------------------------------------


def mapping_path(config_dir: Path | None = None) -> Path:
    return (config_dir or CONFIG_DIR) / MAPPING_FILE


def load_mapping(config_dir: Path | None = None) -> dict[str, str | None]:
    """Column heading -> field id, or None for "ignore this column"."""
    path = mapping_path(config_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    columns = raw.get("columns")
    return columns if isinstance(columns, dict) else {}


def save_mapping(columns: Sequence[Column], config_dir: Path | None = None) -> Path:
    """Remember what staff confirmed, so the same form maps itself next time.

    Only the column headings and the field ids are stored. No answer, no name, no
    date of birth — this file is office configuration, and it is in the repository.
    """
    path = mapping_path(config_dir)
    remembered = load_mapping(config_dir)
    for column in columns:
        if column.is_boilerplate:
            continue
        remembered[column.header] = column.field_id

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "_comment": (
                    "Which column of a client form export feeds which intake field. Written by the "
                    "app when staff confirm an import, and safe to edit by hand. A field id of null "
                    "means 'ignore this column'. Contains no client information — headings only."
                ),
                "columns": dict(sorted(remembered.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
