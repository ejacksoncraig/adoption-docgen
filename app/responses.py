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

#: How a form is likely to refer to each group of fields. A family filling in a
#: form writes "Your date of birth", not "Petitioner — Date of birth", and half a
#: dozen fields are called just "Date of birth" — so the words around the label
#: are the only thing telling the parent's from the child's.
#:
#: A group in fields.json may set its own "aliases" to match the wording of the
#: office's actual form; these are the fallbacks.
#: Keep these specific. A word that turns up inside ordinary questions — "birth"
#: appears in "place of birth", "city of birth", "date of birth" — pulls every one
#: of those columns towards the wrong group.
DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "petitioner1": ("petitioner", "your", "my", "applicant", "adoptive parent", "first petitioner"),
    "petitioner2": ("second petitioner", "spouse", "co petitioner", "your spouse", "step parent"),
    "child1": ("child", "the child", "first child", "minor child"),
    "child2": ("second child", "child 2", "younger child", "sibling"),
    "bio_parents": ("biological", "biological mother", "biological father",
                    "birth mother", "birth father", "natural parent"),
    "stepparent": ("other parent", "other biological parent", "non custodial parent"),
    "dhs": ("dhs", "department of human services", "deprived", "foster"),
    "icwa": ("icwa", "indian child welfare act", "tribal"),
    "case": ("case", "filing county"),
}

#: A fuzzy match this close to the runner-up is not a match, it is a coin toss.
_AMBIGUITY_MARGIN = 0.06

#: Below this, a heading and a field are simply not the same question.
_SIMILARITY_FLOOR = 0.72

_YES = {"yes", "y", "true", "1", "x", "✓", "checked", "on", "yes - please", "affirmative"}
_NO = {"no", "n", "false", "0", "", "unchecked", "off", "none", "n/a", "na"}

_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y/%m/%d",
)

#: Wording a form puts in front of the actual question. "If yes, …" is how every
#: conditional question in a Google Form begins.
_NOISE = re.compile(
    r"^(if (yes|so|applicable)[,:]?\s*)?"
    r"((please\s+)?(enter|provide|tell us|what is|what's|what are|list)\s+"
    r"(the|your|his|her|their)?\s*)?", re.I
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
        """Something human to pick a row by, when a form has several responses.

        Built from the columns whose answers actually *differ* between responses.
        Labelling every row with the same three values — which is what the first
        few columns of a form usually give you — tells staff nothing about which
        family they are about to import.
        """
        count = len(self.rows)
        boilerplate = {normalise(word) for word in BOILERPLATE}
        ordinary = [
            index for index, header in enumerate(self.headers)
            if normalise(header) not in boilerplate
        ]
        varying = [
            index for index in ordinary
            if len({self.value(row, index) for row in range(count)}) > 1
        ]
        useful = varying or ordinary

        labels = []
        for row in range(count):
            parts = [self.value(row, index) for index in useful]
            filled = [part for part in parts if part][:3]
            labels.append(" · ".join(filled) if filled else f"Response {row + 1}")
        return labels


def _delimiter(path: Path, text: str) -> str:
    """Work out what separates the columns.

    csv.Sniffer is unreliable here: an address answer like "100 Main St, Tulsa"
    contains a comma, so a genuinely tab-separated export can sniff as
    comma-separated and collapse into one column. Counting separators in the
    heading row — which holds no free text — is both simpler and right.
    """
    if path.suffix.lower() == ".tsv":
        return "\t"
    heading = text.splitlines()[0] if text.splitlines() else ""
    counts = {candidate: heading.count(candidate) for candidate in ("\t", ",", ";")}
    best = max(counts, key=lambda candidate: counts[candidate])
    return best if counts[best] else ","


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

    rows = [
        row for row in csv.reader(text.splitlines(), delimiter=_delimiter(path, text))
        if any(cell.strip() for cell in row)
    ]
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


def group_aliases(schema: Schema, group_id: str) -> tuple[str, ...]:
    """The words a form might use for a group, from fields.json or the defaults."""
    declared = schema.groups.get(group_id, {}).get("aliases")
    if declared:
        return tuple(declared)
    label = schema.groups.get(group_id, {}).get("label", group_id)
    return (label, *DEFAULT_ALIASES.get(group_id, ()))


def search_keys(schema: Schema, fd: FieldDef) -> list[str]:
    """What a heading is compared against: the field's own words, nothing else.

    Deliberately *not* qualified with the group ("petitioner date of birth"), even
    though several fields share a label. Comparing a heading against a qualified
    key rewards whichever group has the shortest name — "my date of birth" scores
    above "child date of birth" for a bare "Date of birth" purely because "my" is
    two letters. Which group is meant is decided separately, by whether the
    heading actually names one.
    """
    return [k for k in (normalise(fd.label), normalise(fd.id).replace("1", "").replace("2", " 2")) if k]


def strip_group_words(header_key: str, schema: Schema, groups: Sequence[str]) -> str:
    """'child s date of birth' -> 'date of birth', so it can be compared to a label."""
    stripped = header_key
    for group_id in groups:
        for alias in group_aliases(schema, group_id):
            phrase = normalise(alias)
            if phrase:
                stripped = re.sub(rf"\b{re.escape(phrase)}\b", " ", stripped)
    stripped = re.sub(r"\bs\b", " ", stripped)          # the leftover of a possessive
    return re.sub(r"\s+", " ", stripped).strip()


def _similarity(header_key: str, stripped: str, keys: Iterable[str]) -> float:
    """Best resemblance, with and without the group words.

    Both are needed: "Child's date of birth" only matches "Date of birth" once
    "child" is out of the way, while "Biological mother's full name" matches the
    label "Biological mother" precisely *because* those words are still in it.
    """
    return max(
        (
            max(
                difflib.SequenceMatcher(None, header_key, key).ratio(),
                difflib.SequenceMatcher(None, stripped, key).ratio() if stripped else 0.0,
            )
            for key in keys
        ),
        default=0.0,
    )


def groups_named_in(header_key: str, schema: Schema, groups: Sequence[str]) -> set[str]:
    """Which groups a heading names outright: "Your …" the petitioner, "Child's …" the child.

    This is the signal that separates a parent's date of birth from a child's,
    which no amount of comparing "date of birth" to "date of birth" can.

    The most specific mention wins. "Second child's name" names both child2 (by
    "second child") and child1 (by "child"); the longer phrase is the one the
    heading actually meant.
    """
    best: dict[str, int] = {}
    for group_id in groups:
        for alias in group_aliases(schema, group_id):
            phrase = normalise(alias)
            if phrase and re.search(rf"\b{re.escape(phrase)}\b", header_key):
                best[group_id] = max(best.get(group_id, 0), len(phrase))

    if not best:
        return set()
    longest = max(best.values())
    return {group_id for group_id, length in best.items() if length == longest}


#: Weight of naming the right group, and of naming somebody else's.
_GROUP_BONUS = 0.15
_WRONG_GROUP_PENALTY = 0.30


def _score(header_key: str, stripped: str, fd: FieldDef, keys: Iterable[str], named: set[str]) -> float:
    score = _similarity(header_key, stripped, keys)
    if named:
        score += _GROUP_BONUS if fd.group in named else -_WRONG_GROUP_PENALTY
    return score


def match_columns(
    sheet: Sheet,
    schema: Schema,
    groups: Sequence[str],
    remembered: dict[str, str | None] | None = None,
) -> list[Column]:
    """Suggest a field for every column. Confident where it can be, silent where not.

    What staff confirmed last time always wins. Otherwise a column is scored
    against every field's likely phrasings and the best pairings are taken first,
    one field per column. Two rules keep it honest:

    * a column whose best two candidates are near-tied is left unmatched, because
      "Your date of birth" resembling the child's is a coin toss, and a coin toss
      that loses puts a parent's birthday in a child's petition;
    * no field is ever filled from two columns.

    Everything it does decide is shown to staff to confirm before use.
    """
    remembered = remembered or {}
    fields = schema.input_fields(groups)
    by_id = {fd.id: fd for fd in fields}

    keys_for = {fd.id: search_keys(schema, fd) for fd in fields}

    columns = [
        Column(index=index, header=header, samples=sheet.samples(index))
        for index, header in enumerate(sheet.headers)
    ]

    proposals: list[tuple[int, float, Column, str]] = []
    for column in columns:
        if column.is_boilerplate:
            continue

        if column.header in remembered:
            target = remembered[column.header]
            if target is None:
                continue                       # staff said to ignore this column
            if target in by_id:
                proposals.append((0, 1.0, column, target))
                continue

        key = normalise(column.header)
        exact = next((fd.id for fd in fields if normalise(fd.id) == key), None)
        if exact:
            proposals.append((1, 1.0, column, exact))
            continue

        named = groups_named_in(key, schema, groups)
        stripped = strip_group_words(key, schema, groups)
        scored = sorted(
            ((_score(key, stripped, fd, keys_for[fd.id], named), fd.id) for fd in fields),
            reverse=True,
        )
        if not scored or scored[0][0] < _SIMILARITY_FLOOR:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < _AMBIGUITY_MARGIN:
            column.confidence = "ambiguous"    # too close to call; a human decides
            continue

        rank = 2 if scored[0][0] >= 0.97 else 3
        proposals.append((rank, scored[0][0], column, scored[0][1]))

    taken: set[str] = set()
    for rank, _score_value, column, field_id in sorted(
        proposals, key=lambda p: (p[0], -p[1], p[2].index)
    ):
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


# --------------------------------------------------------------------------
# when the form arrives as a PDF
# --------------------------------------------------------------------------
#
# A PDF is a picture of a form, not a form. There are no columns in it — just
# text in an order, or sometimes no text at all. So the job is to find the
# questions and work out where each answer starts and stops, then hand back the
# same Sheet a CSV produces, so everything downstream is unchanged: the same
# matching, the same screen for staff to confirm, the same remembered mapping.
#
# A spreadsheet export is still the better route when it exists. This one is for
# when all the office has is the PDF.

#: Lines a printed form carries that are not questions or answers.
_PDF_NOISE = re.compile(
    r"^(page\s*\d+(\s*(of|/)\s*\d+)?|\d+\s*/\s*\d+|https?://\S+|"
    r".*\bgoogle (forms|docs)\b.*|this (content|form) is neither.*|"
    r".*\bnever submit passwords\b.*)$",
    re.I,
)

#: How closely a line must resemble a known question before it is treated as one.
#: The same bar the column matcher uses; a printed form is no harder to read than
#: a spreadsheet heading, and a stricter bar here silently skipped questions whose
#: wording is loose ("County the child has lived in for the past 5 years").
_PDF_QUESTION_FLOOR = _SIMILARITY_FLOOR

#: A question can wrap onto the next line; an answer rarely runs past this.
_PDF_WRAP_LINES = 2
_PDF_ANSWER_LINES = 6

#: On a printed form the line under a question is its answer. Only something that
#: reads *almost exactly* like another question is taken as one instead — which is
#: what a question the client skipped looks like. Without this, an answer that
#: happens to contain a group word ("SAMPLE PETITIONER") is mistaken for a
#: question and the real answer is lost.
_PDF_SKIPPED_QUESTION = 0.90


def field_resemblance(
    text: str,
    schema: Schema,
    groups: Sequence[str],
    keys_for: dict[str, list[str]] | None = None,
) -> float:
    """How much a piece of text reads like a question about one of these fields.

    Exactly the scoring the column matcher uses — group words and all. A printed
    form and a spreadsheet heading ask the same questions in the same words, so
    judging them by two different standards only meant the PDF reader skipped
    questions the CSV reader had no trouble with.
    """
    fields = schema.input_fields(groups)
    keys_for = keys_for or {fd.id: search_keys(schema, fd) for fd in fields}
    key = normalise(text)
    if not key:
        return 0.0
    named = groups_named_in(key, schema, groups)
    stripped = strip_group_words(key, schema, groups)
    return max(
        (_score(key, stripped, fd, keys_for[fd.id], named) for fd in fields),
        default=0.0,
    )


def pdf_lines(path: Path) -> list[str]:
    """Every line of text in the PDF, in reading order, noise dropped."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
    except (PdfReadError, OSError, ValueError) as exc:
        raise ValueError(f"{path.name} could not be opened as a PDF ({exc})") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")            # many PDFs are "encrypted" with no password
        except Exception:
            raise ValueError(
                f"{path.name} is password-protected. Open it, save an unprotected copy, "
                f"and try that."
            ) from None

    lines: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:                  # one unreadable page must not lose the rest
            continue
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if _PDF_NOISE.match(line):
                continue
            # Blank lines are kept: on a printed form the gap under an answer is
            # the clearest signal there is that the answer has finished.
            if line or (lines and lines[-1]):
                lines.append(line)
    return lines


def pdf_form_fields(path: Path) -> dict[str, str]:
    """Values of a fillable PDF's own form fields, if it has any.

    A PDF filled in with Acrobat or Edge carries its answers as named fields.
    That is exact — no guessing where an answer begins — so it is tried first.
    """
    from pypdf import PdfReader

    try:
        fields = PdfReader(str(path)).get_fields() or {}
    except Exception:
        return {}

    found: dict[str, str] = {}
    for name, field in fields.items():
        value = field.get("/V") if hasattr(field, "get") else None
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        text = str(value).strip().lstrip("/")
        if text and text.lower() not in {"off", "none"}:
            found[str(name).split(".")[-1].strip()] = text
    return found


def _question_at(lines: list[str], index: int, resembles) -> tuple[str, int] | None:
    """Is a question starting here? Returns (its text, how many lines it took).

    A single line is always preferred. Two lines are joined only when neither is
    a question by itself — otherwise an answer gets swallowed into the question
    below it ("Male" + "Hospital or place of birth" reads as one long question,
    and the gender answer disappears).
    """
    if index >= len(lines) or not lines[index]:
        return None

    alone = resembles(lines[index])
    if alone >= _PDF_QUESTION_FLOOR:
        return lines[index], 1

    for span in range(2, _PDF_WRAP_LINES + 1):
        window = lines[index : index + span]
        if len(window) < span or not all(window):
            break
        if any(resembles(line) >= _PDF_QUESTION_FLOOR for line in window[1:]):
            break
        joined = " ".join(window)
        if resembles(joined) >= _PDF_QUESTION_FLOOR:
            return joined, span
    return None


def read_pdf(
    path: Path,
    schema: Schema | None = None,
    groups: Sequence[str] = (),
    remembered: dict[str, str | None] | None = None,
) -> Sheet:
    """Read a client's form from a PDF into the same shape a CSV export gives.

    Tries the PDF's own form fields first — a filled-in fillable PDF carries its
    answers exactly, with no guessing where one stops. Otherwise it finds the
    questions in the printed text and takes what follows each one as its answer.
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(f"{path} does not exist")

    fields = pdf_form_fields(path)
    if fields:
        return Sheet(path=path, headers=list(fields), rows=[list(fields.values())])

    lines = pdf_lines(path)
    if not lines:
        raise ValueError(
            f"{path.name} has no text in it — it is a scan or a photograph of a form, "
            f"and there is nothing in the file to read. Type it in by hand, or ask for "
            f"the responses as a spreadsheet (in Google Forms: Responses, open in "
            f"Sheets, then File, Download, Comma-separated values)."
        )
    if schema is None:
        raise ValueError(f"{path.name}: no questions to look for — choose the adoption type first")

    keys_for = {fd.id: search_keys(schema, fd) for fd in schema.input_fields(groups)}
    confirmed = {normalise(heading) for heading, target in (remembered or {}).items() if target}

    def resembles(text: str) -> float:
        """A heading staff already confirmed is a question, whatever it looks like."""
        if normalise(text) in confirmed:
            return 1.0
        return field_resemblance(text, schema, groups, keys_for)

    headers: list[str] = []
    answers: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index]:
            index += 1
            continue
        found = _question_at(lines, index, resembles)
        if not found:
            index += 1
            continue

        question, span = found
        index += span
        collected: list[str] = []
        while index < len(lines) and len(collected) < _PDF_ANSWER_LINES:
            if not lines[index]:            # the gap under the answer
                break
            following = _question_at(lines, index, resembles)
            if following:
                # The first line under a question is its answer unless it is
                # unmistakably the next question, i.e. this one went unanswered.
                if collected or resembles(following[0]) >= _PDF_SKIPPED_QUESTION:
                    break
            collected.append(lines[index])
            index += 1

        headers.append(question)
        answers.append(" ".join(collected).strip())

    if not headers:
        raise ValueError(
            f"{path.name} does not appear to hold answers to this kind of adoption — "
            f"none of its text matches the questions for the type you selected. Check "
            f"you picked the right adoption type, or import the spreadsheet export instead."
        )
    return Sheet(path=path, headers=headers, rows=[answers])


def read_any(
    path: Path,
    schema: Schema | None = None,
    groups: Sequence[str] = (),
    remembered: dict[str, str | None] | None = None,
) -> Sheet:
    """Read a client's completed form, whatever shape it arrived in."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return read_pdf(path, schema, groups, remembered)
    return read_sheet(path)
