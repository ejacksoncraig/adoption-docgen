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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from app import __version__
from app.registry import INTAKE_DIR, OUTPUT_DIR, Registry
from app.schema import FieldDef, Schema

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

_ANSWER_LINE = "_" * 58

_INSTRUCTIONS = (
    "Please answer every question below as completely as you can, then return "
    "this form to our office. Where a question does not apply, write N/A rather "
    "than leaving it blank, so we know it was not overlooked. Dates should be "
    "written as month/day/year."
)


def _prompt_for(fd: FieldDef) -> str:
    if fd.type == "bool":
        return "Yes  /  No"
    if fd.type == "select":
        return "  /  ".join(o.capitalize() for o in fd.options)
    if fd.type == "date":
        return "____ /____ /________   (month / day / year)"
    return _ANSWER_LINE


def build_questionnaire(registry: Registry, matter_id: str, variant_id: str, out_path: Path) -> Path:
    """Generate a blank .docx questionnaire for a variant, straight from the schema.

    Adding a field to fields.json adds it here too. There is no second list to
    keep in step.
    """
    variant = registry.variant(matter_id, variant_id)
    matter = registry.matter(matter_id)
    schema = registry.schema

    doc = docx.Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("ADOPTION INTAKE QUESTIONNAIRE")
    run.bold = True
    run.font.size = Pt(14)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(f"{matter.label} — {variant.label}").italic = True

    doc.add_paragraph(_INSTRUCTIONS)

    for group_id in schema.group_order(variant.field_groups):
        # Office information is not a question for the family — see Schema.on_questionnaire.
        fields = [fd for fd in schema.input_fields([group_id]) if schema.on_questionnaire(fd)]
        if not fields:
            continue
        heading = doc.add_paragraph()
        heading.paragraph_format.space_before = Pt(14)
        heading.add_run(schema.groups[group_id].get("label", group_id).upper()).bold = True

        for fd in fields:
            label = fd.label + ("" if fd.required else "  (if applicable)")
            question = doc.add_paragraph()
            question.paragraph_format.space_before = Pt(8)
            question.paragraph_format.space_after = Pt(0)
            question.add_run(f"{label}:")
            if fd.help:
                note = doc.add_paragraph()
                note.paragraph_format.space_after = Pt(0)
                note.add_run(fd.help).italic = True
            answer = doc.add_paragraph(_prompt_for(fd))
            answer.paragraph_format.space_after = Pt(6)

    footer = doc.add_paragraph()
    footer.paragraph_format.space_before = Pt(18)
    footer.add_run(
        "Signature: " + "_" * 34 + "        Date: " + "_" * 18
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def default_questionnaire_path(matter_id: str, variant_id: str, directory: Path | None = None) -> Path:
    directory = directory or OUTPUT_DIR
    return directory / f"questionnaire_{matter_id}_{variant_id}.docx"


# --------------------------------------------------------------------------
# the browser form
# --------------------------------------------------------------------------

#: Where the generated form gets its questions injected.
_SPEC_MARKER = "/*__SPEC__*/null"


def client_form_spec(registry: Registry, matter_id: str, variant_id: str) -> dict[str, Any]:
    """The questions to put in front of the family — theirs only.

    Office fields (filing county, case number, the fee summary) are left out, the
    same ones the paper questionnaire leaves out. The family should not be asked
    for something they cannot know, and every extra question is one more chance
    to be told something wrong.
    """
    variant = registry.variant(matter_id, variant_id)
    matter = registry.matter(matter_id)
    schema = registry.schema

    groups = []
    for group_id in schema.group_order(variant.field_groups):
        fields = [fd for fd in schema.input_fields([group_id]) if schema.on_questionnaire(fd)]
        if not fields:
            continue
        groups.append(
            {
                "id": group_id,
                "label": schema.groups[group_id].get("label", group_id),
                "fields": [
                    {
                        "id": fd.id,
                        "label": fd.label,
                        "type": fd.type,
                        "required": fd.required,
                        "options": list(fd.options),
                        "help": fd.help,
                        "depends_on": fd.depends_on,
                    }
                    for fd in fields
                ],
            }
        )

    return {
        "matter": matter_id,
        "matter_label": matter.label,
        "variant": variant_id,
        "variant_label": variant.label,
        "generated": date.today().isoformat(),
        "groups": groups,
    }


def build_client_form(registry: Registry, matter_id: str, variant_id: str, out_path: Path) -> Path:
    """Write the .html questionnaire the office emails to the family.

    The result is one file with the questions baked into it — no separate data
    file to lose, and nothing to fetch, so it works on a machine with no network
    at all.
    """
    template_path = UI_DIR / "client_form.html"
    if not template_path.exists():
        raise ValueError(f"the client form template is missing: {template_path}")

    template = template_path.read_text(encoding="utf-8")
    if _SPEC_MARKER not in template:
        raise ValueError(f"{template_path.name} has no {_SPEC_MARKER} marker to inject the questions into")

    spec = client_form_spec(registry, matter_id, variant_id)
    # </script> inside a string literal would end the block early; nothing in the
    # schema should contain one, but a stray one must not break the whole form.
    payload = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(template.replace(_SPEC_MARKER, payload), encoding="utf-8")
    return out_path


def default_client_form_path(matter_id: str, variant_id: str, directory: Path | None = None) -> Path:
    directory = directory or OUTPUT_DIR
    return directory / f"Adoption questionnaire - {matter_id} {variant_id}.html"


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
