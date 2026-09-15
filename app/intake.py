"""Saved intake files, and the two questionnaires the office sends to a family.

There is deliberately no hosted form, no server and no accounts. Two ways to ask
a family the same questions, both by email:

``build_questionnaire``  a plain .docx to print, fill in by hand, and send back.
                         Staff type the answers in. Lowest common denominator.
``build_client_form``    a single self-contained .html file. The family opens it
                         in any browser, answers, and clicks Save; the browser
                         writes a small .json file they email back, which
                         ``load_intake`` reads straight into the intake form.

The second one exists to remove the retyping step, which is where a
misspelled name enters a petition. Neither transmits anything: the .html has no
network code in it at all, and the file comes back the way any attachment does.

Both are generated from the same field schema, so a field added to
config/fields.json appears on the paper form, the browser form, and the office's
own intake form from that one edit.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt

from app import __version__
from app.registry import INTAKE_DIR, OUTPUT_DIR, Registry
from app.schema import FieldDef, Schema, as_bool

INTAKE_FORMAT = 1


# --------------------------------------------------------------------------
# save / load
# --------------------------------------------------------------------------


def default_intake_name(matter_id: str, variant_id: str, values: dict[str, Any], today: date | None = None) -> str:
    from app.engine import safe_filename

    today = today or date.today()
    subject = values.get("child1_name") or values.get("petitioner1_name") or variant_id
    return safe_filename(f"{matter_id}_{subject}_{today.isoformat()}.json").replace(" ", "_")


def save_intake(
    matter_id: str,
    variant_id: str,
    values: dict[str, Any],
    path: Path | None = None,
    directory: Path | None = None,
) -> Path:
    """Write an in-progress intake so it can be reopened. Never goes in the repo."""
    directory = directory or INTAKE_DIR
    path = path or directory / default_intake_name(matter_id, variant_id, values)
    payload = {
        "format": INTAKE_FORMAT,
        "app_version": __version__,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "matter": matter_id,
        "variant": variant_id,
        "values": values,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_intake(path: Path, registry: Registry | None = None) -> dict[str, Any]:
    """Read a saved intake, or a questionnaire a family filled in and sent back.

    Both are JSON and both carry ``matter``, ``variant`` and ``values``; a
    returned questionnaire is marked ``"kind": "client_form"`` so the office can
    be told where the answers came from and what is left to fill in.

    Anything the current config no longer knows about is dropped and reported
    rather than silently carried along — a renamed field must not end up filling
    nothing on the form while looking like it was answered. The same goes for a
    family who edited the file by hand: values are checked against the schema
    here, not trusted.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not a valid intake file: {exc.msg} (line {exc.lineno})") from exc
    except UnicodeDecodeError:
        raise ValueError(
            f"{path.name} is not a file this app can read. If the family sent back the "
            f"questionnaire itself rather than their saved answers, ask them to open it "
            f"and click 'Save my answers'."
        ) from None

    if not isinstance(raw, dict) or not isinstance(raw.get("values"), dict):
        raise ValueError(f"{path.name} is not an intake file saved by this app")

    from_client = raw.get("kind") == "client_form"
    values = dict(raw["values"])
    warnings: list[str] = []

    if registry is not None:
        matter_id, variant_id = raw.get("matter"), raw.get("variant")
        try:
            variant = registry.variant(matter_id, variant_id)
        except KeyError:
            raise ValueError(
                f"{path.name} was saved for {matter_id}/{variant_id}, which this app no longer offers"
            ) from None

        in_scope = registry.schema.ids_for_groups(variant.field_groups)
        for key in sorted(k for k in values if k not in in_scope):
            values.pop(key)
            warnings.append(f"{key}: no longer part of this variant, not loaded")

        problems = registry.schema.validate(values, variant.field_groups, include_required=False)
        for problem in problems:
            warnings.append(problem)

    return {
        "matter": raw.get("matter"),
        "variant": raw.get("variant"),
        "saved_at": raw.get("saved_at"),
        "values": values,
        "warnings": warnings,
        "from_client": from_client,
        "answered": sorted(values),
    }


def list_intakes(directory: Path | None = None) -> list[dict[str, Any]]:
    directory = directory or INTAKE_DIR
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "path": str(path),
                "name": path.name,
                "matter": raw.get("matter"),
                "variant": raw.get("variant"),
                "saved_at": raw.get("saved_at"),
            }
        )
    return out


# --------------------------------------------------------------------------
# the paper questionnaire
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# the one-page form
# --------------------------------------------------------------------------
#
# Modelled on the sheet the office already used: every answer on one page, two
# to a line, each on a rule you can write along. A question per block with its
# help text underneath is easier to read and runs to four pages, which is four
# pages to carry into a meeting and four to type back from. This is the shape
# that gets used.

#: Body size. 12pt is the filing's size, not a form's; at 10pt the whole intake
#: fits on one side of one sheet with room left to write.
FORM_PT = Pt(10)

#: Half-inch page margins all round, which is what buys the extra rows.
FORM_MARGIN_IN = 0.5

#: Fields that need the width of the page rather than half of it. Anything
#: somebody writes a sentence into, plus the ones whose answers are simply long.
_FULL_WIDTH = ("address", "full legal name", "fees and costs", "city and state",
               "relationship to the child", "hospital or place of birth",
               "current legal name", "new legal name")


def _wants_full_width(fd: FieldDef) -> bool:
    if fd.multiline:
        return True
    return any(phrase in fd.label.lower() for phrase in _FULL_WIDTH)


#: Marks a question that is only asked when the box it depends on is ticked. On
#: paper a blank is otherwise ambiguous between "not asked" and "asked, and the
#: answer is no", and the long form said so in words it no longer has room for.
CONDITIONAL_MARK = "\u2020"


def _answer_hint(fd: FieldDef) -> str:
    """What goes in the space: the choices, or nothing and a rule to write on."""
    if fd.type == "bool":
        return "Y  /  N"
    if fd.type == "select" and fd.options and not fd.searchable:
        return "  /  ".join(o.capitalize() for o in fd.options)
    return ""


def _form_label(fd: FieldDef) -> str:
    mark = CONDITIONAL_MARK if fd.depends_on else ""
    return f"{fd.short_label or fd.label}{mark}:"


def _rule(cell, text: str) -> None:
    """A cell with a line along the bottom, or the choices to circle."""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.space_before = Pt(0)
    run = paragraph.add_run(text)
    run.font.size = FORM_PT
    if text:
        return                                  # choices, not a blank to fill
    borders = OxmlElement("w:tcBorders")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "000000")
    borders.append(bottom)
    cell._tc.get_or_add_tcPr().append(borders)


def _caption(cell, text: str, *, bold: bool = False) -> None:
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.space_before = Pt(0)
    run = paragraph.add_run(text)
    run.font.size = FORM_PT
    run.bold = bold


def _form_table(doc, columns: list[int]):
    table = doc.add_table(rows=0, cols=len(columns))
    table.autofit = False
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "none")
        borders.append(element)
    tblPr.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for edge, width in (("top", 0), ("left", 0), ("bottom", 0), ("right", 60)):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:w"), str(width))
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    tblPr.append(margins)
    return table


def build_one_page_form(
    registry: Registry, matter_id: str, variant_id: str, out_path: Path,
    *, title: str, intro: str, only_for_the_family: bool,
) -> Path:
    """Every question this matter asks, on one page, in the office's own shape.

    ``only_for_the_family`` leaves out what the office fills in for itself — the
    filing county, the fee summary — which is the single difference between the
    sheet posted to adoptive parents and the one an attorney takes into an
    interview. Both are built from the schema, so a field added to fields.json
    appears on whichever of them it belongs to without a second list to keep.
    """
    variant = registry.variant(matter_id, variant_id)
    matter = registry.matter(matter_id)
    schema = registry.schema

    doc = docx.Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = FORM_PT
    style.paragraph_format.space_after = Pt(0)
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(FORM_MARGIN_IN)
        section.left_margin = section.right_margin = Inches(FORM_MARGIN_IN)

    width = Emu(section.page_width - section.left_margin - section.right_margin).twips
    label_w, answer_w = int(width * 0.27), int(width * 0.23)

    heading = doc.add_paragraph()
    run = heading.add_run(title)
    run.bold = True
    run.font.size = Pt(12)
    heading.add_run(f"        {matter.label} — {variant.label}").font.size = Pt(9)
    heading.add_run("        Date: ______________").font.size = FORM_PT

    note = doc.add_paragraph()
    note.paragraph_format.space_after = Pt(4)
    note.add_run(intro).font.size = Pt(8)

    table = _form_table(doc, [label_w, answer_w, label_w, answer_w])

    for group_id in schema.group_order(variant.field_groups):
        fields = [fd for fd in schema.input_fields([group_id])
                  if not only_for_the_family or schema.on_questionnaire(fd)]
        if not fields:
            continue

        row = table.add_row().cells
        row[0].merge(row[-1])
        _caption(row[0], schema.groups[group_id].get("label", group_id).upper(), bold=True)
        row[0].paragraphs[0].paragraph_format.space_before = Pt(5)

        queue = list(fields)
        while queue:
            fd = queue.pop(0)
            cells = table.add_row().cells
            if _wants_full_width(fd):
                _caption(cells[0], _form_label(fd))
                cells[1].merge(cells[3])
                _rule(cells[1], _answer_hint(fd))
                continue
            _caption(cells[0], _form_label(fd))
            _rule(cells[1], _answer_hint(fd))
            if queue and not _wants_full_width(queue[0]):
                other = queue.pop(0)
                _caption(cells[2], _form_label(other))
                _rule(cells[3], _answer_hint(other))

    if any(fd.depends_on for group_id in schema.group_order(variant.field_groups)
           for fd in schema.input_fields([group_id])
           if not only_for_the_family or schema.on_questionnaire(fd)):
        footnote = doc.add_paragraph()
        footnote.paragraph_format.space_before = Pt(4)
        run = footnote.add_run(
            f"{CONDITIONAL_MARK} asked only when the box it follows is ticked; "
            f"leave it blank otherwise.")
        run.font.size = Pt(8)
        run.italic = True

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def build_questionnaire(registry: Registry, matter_id: str, variant_id: str, out_path: Path) -> Path:
    """The sheet posted to adoptive parents: their questions only, on one page."""
    return build_one_page_form(
        registry, matter_id, variant_id, out_path,
        title="ADOPTION INTAKE — FOR ADOPTING PARENTS",
        intro="Please answer everything you can, then return this to our office. Where a "
              "question does not apply write N/A rather than leaving it blank, so we know "
              "it was not overlooked. Dates as month/day/year.",
        only_for_the_family=True,
    )


# --------------------------------------------------------------------------
# the office's own worksheet
# --------------------------------------------------------------------------

def build_intake_worksheet(
    registry: Registry, matter_id: str, variant_id: str, out_path: Path
) -> Path:
    """The office's own sheet: every question the matter asks, on one page.

    The questionnaire above leaves out what the office fills in for itself. This
    keeps all of it, for an attorney to take an interview on and type up after.
    """
    return build_one_page_form(
        registry, matter_id, variant_id, out_path,
        title="ADOPTION INTAKE",
        intro="Every question this matter asks, in the order the intake form asks them — "
              "including the ones the family is never asked.",
        only_for_the_family=False,
    )


def default_worksheet_path(matter_id: str, variant_id: str, directory: Path | None = None) -> Path:
    directory = directory or OUTPUT_DIR
    return directory / f"intake_worksheet_{matter_id}_{variant_id}.docx"


def default_questionnaire_path(matter_id: str, variant_id: str, directory: Path | None = None) -> Path:
    directory = directory or OUTPUT_DIR
    return directory / f"questionnaire_{matter_id}_{variant_id}.docx"


# --------------------------------------------------------------------------
# obviously-fake values, for smoke rendering and the golden test
# --------------------------------------------------------------------------

_SAMPLE_TEXT = {
    "name": "SAMPLE PETITIONER",
    "address": "100 Sample Street, Sampletown, OK 74000",
    "race": "Caucasian",
    "place": "Sample Regional Hospital",
    "city": "Sampletown",
    "county": "Sample",
    "state": "Oklahoma",
    "tribe": "Sample Nation",
}


def sample_values(schema: Schema, groups: Iterable[str], *, truthy: bool = True) -> dict[str, Any]:
    """A complete set of placeholder answers.

    Deliberately unmistakable — no realistic names ever go into this repository,
    including in fixtures.

    ``truthy=False`` takes the other side of every branch: booleans go false and
    selects take their last option instead of their first. Rendering a variant
    both ways is how you read both halves of every conditional in a template.

    Fields that declare a ``default`` are left blank on purpose, so the default
    is what gets exercised.
    """
    values: dict[str, Any] = {}
    for fd in schema.input_fields(groups):
        if fd.default is not None:
            continue
        if fd.type == "bool":
            values[fd.id] = truthy
        elif fd.type == "select":
            values[fd.id] = (fd.options[0] if truthy else fd.options[-1]) if fd.options else ""
        elif fd.type == "date":
            values[fd.id] = _sample_date(fd)
        elif fd.type == "number":
            values[fd.id] = 1
        else:
            values[fd.id] = _sample_text(fd)
    return values


def _sample_date(fd: FieldDef) -> str:
    """An adult date of birth for adults, a child's for children, else recent."""
    if fd.id.endswith("_dob"):
        return "2015-03-02" if fd.id.startswith("child") else "1985-06-15"
    return "2024-09-21"


def sample_settings(schema: Schema) -> dict[str, str]:
    """Placeholder office settings, so a smoke render does not need the real ones.

    Real generation still fails loudly when config/settings.json is empty — this
    is only ever merged in when placeholder answers are already being used.
    """
    return {
        fd.id: f"SAMPLE {fd.id.removeprefix('attorney_').replace('_', ' ').upper()}"
        for fd in schema.fields.values()
        if fd.derived and fd.id.startswith("attorney_")
    }


def _sample_text(fd: FieldDef) -> str:
    for key, text in _SAMPLE_TEXT.items():
        if fd.id.endswith(f"_{key}") or fd.id == key:
            if key == "name":
                who = fd.id.rsplit("_", 1)[0].replace("_", " ").upper()
                return f"SAMPLE {who}"
            return text
    return f"SAMPLE {fd.id.replace('_', ' ').upper()}"


# --------------------------------------------------------------------------
# a whole test matter, at one click
# --------------------------------------------------------------------------

#: Answers that read properly in the document rather than as a generic placeholder.
#: Nothing here is a name — see the note in random_values about why.
_SHAPED = {
    "attorney_fees_summary": (
        "Hourly Rate = $300.00; Total Hours = {hours}.00 x $300.00 = ${fees:,}.00; "
        "Filing Fee = $184.14; Amended Birth Certificate = $40.00; Total = ${total:,.2f}."
    ),
    "address": "{number} Sample Street, Sampletown, OK 74{zip3}",
    "place": "Sample {kind} Hospital",
    "city": "Sampletown",
    "county": "Sample",
    "state": "Oklahoma",
    "race": "Caucasian",
    "tribe": "Sample Nation",
}

_HOSPITAL_KINDS = ("Regional", "Memorial", "Community", "General", "County")


def random_values(
    schema: Schema, groups: Iterable[str], seed: int | None = None
) -> dict[str, Any]:
    """A complete, valid, obviously-fake intake — a different one each time.

    For showing the program and checking output quickly. Every run varies the
    dates, the county, the genders and every yes/no and either/or answer, so
    successive runs exercise *different branches* of the templates: ICWA on then
    off, relinquished then terminated, a name change and none.

    Names stay unmistakably fake, with a SAMPLE prefix and a number. That is not
    squeamishness: a generated petition reading "Jennifer Watson" could be
    mistaken for a real matter by whoever finds it in the output folder, and the
    documents this produces are otherwise indistinguishable from real ones.
    """
    rng = random.Random(seed)
    tag = rng.randrange(1000, 9999)
    today = date.today()

    values: dict[str, Any] = {}
    for fd in schema.input_fields(groups):
        if fd.default is not None:
            continue                    # leave it blank so the default is exercised
        if fd.type == "bool":
            values[fd.id] = rng.random() < 0.5
        elif fd.type == "select":
            values[fd.id] = rng.choice(fd.options) if fd.options else ""
        elif fd.type == "date":
            values[fd.id] = _random_date(fd, rng, today)
        elif fd.type == "number":
            values[fd.id] = rng.randrange(1, 4)
        else:
            values[fd.id] = _random_text(fd, rng, tag)

    # A question behind an unticked box has no answer; leaving one in would be
    # an answer to a question that was never asked.
    for fd in schema.input_fields(groups):
        if fd.depends_on and not as_bool(values.get(fd.depends_on)):
            values.pop(fd.id, None)
    return values


def _random_date(fd: FieldDef, rng: "random.Random", today: date) -> str:
    """A date that makes sense for what it is: children are children, adults adults."""
    if fd.id.endswith("_dob"):
        span = (1, 17) if fd.id.startswith("child") else (24, 55)
    elif "marriage" in fd.id:
        span = (2, 20)
    else:                                # placements, filings: the recent past
        span = (1, 6)
    years = rng.randint(*span)
    days = rng.randrange(0, 365)
    return (today - timedelta(days=years * 365 + days)).isoformat()


def _random_text(fd: FieldDef, rng: "random.Random", tag: int) -> str:
    if fd.id == "attorney_fees_summary":
        hours = rng.randrange(8, 20)
        fees = hours * 300
        return _SHAPED[fd.id].format(hours=hours, fees=fees, total=fees + 184.14 + 40)

    for key, shape in _SHAPED.items():
        if fd.id == key or fd.id.endswith(f"_{key}"):
            return shape.format(
                number=rng.randrange(100, 9999),
                zip3=f"{rng.randrange(0, 999):03d}",
                kind=rng.choice(_HOSPITAL_KINDS),
            )

    if fd.id.endswith("_name") or fd.id == "name":
        who = fd.id.rsplit("_", 1)[0].replace("_", " ").upper()
        return f"SAMPLE {who} {tag}"
    return f"SAMPLE {fd.label.upper()} {tag}"
