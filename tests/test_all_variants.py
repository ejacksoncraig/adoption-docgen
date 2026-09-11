"""Every ready variant renders, both ways, with nothing left behind.

This is the net that catches a newly added template before anyone finds out from
a court clerk. It does not read the documents — only a person can tell whether a
paragraph still makes sense — but it proves that every variant the UI offers can
actually be generated, that no `XXX` or Jinja tag survives, and that the package
is a valid .docx.

Add per-branch assertions for a template as well; see tests/test_render.py and
tests/test_stepparent.py.
"""

from __future__ import annotations

import collections
import re
import zipfile

import pytest

from app import engine, intake
from app.schema import as_bool

TOKENS = ("XXX", "{{", "{%", "}}", "%}")


def ready_variants(registry):
    return [(m.id, v.id) for m in registry.matters for v in m.variants if v.is_ready]


def variant_ids(registry):
    return [f"{m}/{v}" for m, v in ready_variants(registry)]


@pytest.fixture(scope="module")
def cases(registry):
    return ready_variants(registry)


def test_every_ready_variant_is_offered_in_the_ui(registry):
    offered = {(m["id"], v["id"]) for m in registry.catalog() for v in m["variants"]}
    assert offered == set(ready_variants(registry))
    assert offered, "the UI would show nothing at all"


@pytest.mark.parametrize("truthy", [True, False], ids=["yes-branches", "no-branches"])
def test_every_variant_renders_cleanly(registry, tmp_path, today, truthy):
    failures: list[str] = []

    for matter_id, variant_id in ready_variants(registry):
        variant = registry.variant(matter_id, variant_id)
        values = intake.sample_values(registry.schema, variant.field_groups, truthy=truthy)
        try:
            result = engine.generate(registry, matter_id, variant_id, values,
                                     today=today, output_root=tmp_path / f"{variant_id}_{truthy}")
        except Exception as exc:  # noqa: BLE001 - collect them all, report together
            failures.append(f"{matter_id}/{variant_id}: {exc}")
            continue

        expected = len([
            d for d in variant.all_documents()
            if d.depends_on is None or as_bool(values.get(d.depends_on))
        ])
        if len(result.files) != expected:
            failures.append(f"{variant_id}: wrote {len(result.files)} documents, expected {expected}")

        for generated in result.files:
            text = engine.document_text(generated.path)
            left = [t for t in TOKENS if t in text]
            if left:
                failures.append(f"{variant_id}/{generated.path.name}: leftover {left}")

            names = zipfile.ZipFile(generated.path).namelist()
            duplicated = [n for n, count in collections.Counter(names).items() if count > 1]
            if duplicated:
                failures.append(f"{variant_id}/{generated.path.name}: duplicate zip entries {duplicated}")

    assert not failures, "\n".join(failures)


def test_every_variant_can_produce_a_questionnaire(registry, tmp_path):
    for matter_id, variant_id in ready_variants(registry):
        path = intake.build_questionnaire(registry, matter_id, variant_id,
                                          tmp_path / f"{variant_id}.docx")
        text = engine.document_text(path)
        assert "ADOPTION INTAKE QUESTIONNAIRE" in text
        assert not [t for t in TOKENS if t in text]


def test_placeholder_answers_satisfy_every_variant(registry):
    """If this fails, a required field was added that sample_values cannot fill —
    which also means `python -m app.cli render` stopped working for that variant."""
    for matter_id, variant_id in ready_variants(registry):
        groups = registry.variant(matter_id, variant_id).field_groups
        for truthy in (True, False):
            values = intake.sample_values(registry.schema, groups, truthy=truthy)
            assert registry.schema.missing_required(values, groups) == [], f"{variant_id} (truthy={truthy})"
            assert registry.schema.validate(values, groups) == [], f"{variant_id} (truthy={truthy})"


# --------------------------------------------------------------------------
# where the numbered allegations sit on the page
# --------------------------------------------------------------------------
#
# One rule across every template: a numbered paragraph's first line is indented
# half an inch, so the number sits at 0.5" and one tab carries its text to the
# next stop. Converting the office's .doc originals had left them at 1", and the
# originals themselves were inconsistent — the notice ran two levels, with its
# rights list a further half-inch in. The office asked for the single rule.
#
# This is a whole-page defect rather than a wrong value, so nothing else in the
# suite would catch it drifting: a re-converted template would simply come back
# at 1" and every value in it would still be right.

PARAGRAPH = re.compile(r'<w:p\b(?:(?!</w:p>).)*?</w:p>', re.S)
FIRST_LINE = re.compile(r'<w:ind[^>]*w:firstLine="(\d+)"[^>]*/>')

#: Twentieths of a point: half an inch.
NUMBERED_INDENT = "720"


def paragraph_content(block: str) -> str:
    """Text and tabs in document order. A <w:tab/> is not a <w:t>."""
    return "".join(
        "\t" if m.group(0).startswith("<w:tab") else m.group(1)
        for m in re.finditer(r'<w:tab\s*/>|<w:t(?:\s[^>]*)?>(.*?)</w:t>', block, re.S)
    )


#: A numbered allegation, however it was typed. Matching only "1.<tab>" at the
#: very start missed three real ways the office's documents write the same thing:
#: tabs typed in front of the number to push it across, spaces instead of tabs
#: after it, and both. Each of those hung differently on the page and none of
#: them was caught until someone read a printed filing.
NUMBERED = re.compile(r"[ \t]*\d+\.[ \t]")


def numbered_paragraphs(path):
    """Every numbered allegation in a template, with its first-line indent."""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    for block in PARAGRAPH.findall(xml):
        text = paragraph_content(block)
        if not NUMBERED.match(text):
            continue
        indent = FIRST_LINE.search(block)
        yield (indent.group(1) if indent else "0"), text


def template_paths(registry):
    seen = {
        registry.template_path(doc.template)
        for matter in registry.matters
        for variant in matter.variants
        for doc in variant.all_documents()
    }
    return sorted(seen)


def test_every_numbered_paragraph_is_indented_half_an_inch(registry):
    offenders = []
    for path in template_paths(registry):
        for indent, text in numbered_paragraphs(path):
            if indent != NUMBERED_INDENT:
                offenders.append(f"{path.name}: first-line {indent} on {text[:50]!r}")
    assert not offenders, (
        "numbered paragraphs not at a half-inch first-line indent:\n" + "\n".join(offenders)
    )


def test_a_numbered_paragraph_is_pushed_across_by_its_indent_alone(registry):
    """Not by tabs typed in front of the number, and not by a space after it.

    Both look right in the one document somebody checked and wrong beside the
    allegations above them, because the indent and the typed padding add up."""
    offenders = []
    for path in template_paths(registry):
        for _indent, text in numbered_paragraphs(path):
            if not re.match(r"\d+\.\t", text):
                offenders.append(f"{path.name}: {text[:50]!r}")
    assert not offenders, "numbered paragraphs padded by hand:\n" + "\n".join(offenders)


def test_every_template_has_numbered_paragraphs_to_check(registry):
    """Guards the guard: if the "1.<tab>" shape ever changes, the test above
    would pass by finding nothing rather than by finding it correct."""
    counted = {path.name: len(list(numbered_paragraphs(path)))
               for path in template_paths(registry)}
    assert all(counted.values()), f"no numbered paragraphs found in: {counted}"


# --------------------------------------------------------------------------
# the caption, and rules on the page
# --------------------------------------------------------------------------
#
# The caption used to be drawn with tab stops: each line tabbed out to a ")" and
# once more to the case number. That lines up only while the text beside it stays
# short — a long child's name pushes its own ")" to the next stop and breaks the
# vertical rule, on the first page of a filing, where nobody re-reads it. It is
# a 1x3 table now, which cannot drift.

TABLE = re.compile(r'<w:tbl>.*?</w:tbl>', re.S)
GRID_COL = re.compile(r'<w:gridCol\b')
VISIBLE_RULE = re.compile(r'w:val="(single|double|dashed|dotted|thick|wave)"')
BORDERS = re.compile(r'<w:tblBorders>.*?</w:tblBorders>|<w:tcBorders>.*?</w:tcBorders>', re.S)


def tables(path):
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return TABLE.findall(xml)


def test_no_table_in_any_template_prints_a_rule(registry):
    """These are layout tables — a caption, an address block, a receipt grid.
    A printed border on any of them would be a box drawn across a court filing."""
    offenders = []
    for path in template_paths(registry):
        for i, table in enumerate(tables(path)):
            declared = "".join(BORDERS.findall(table))
            if not declared:
                offenders.append(f"{path.name} table {i}: declares no borders at all")
            elif VISIBLE_RULE.search(declared):
                offenders.append(f"{path.name} table {i}: has a visible rule")
    assert not offenders, "\n".join(offenders)


def test_every_caption_is_a_one_row_three_column_table(registry):
    """One caption per document within a template — the packets hold several.

    Matched on shape, one row by three columns, which is what tells a caption
    apart from the notice's other three-column table (the receipt grid, which has
    a row per addressee)."""
    for path in template_paths(registry):
        captions = [t for t in tables(path)
                    if len(GRID_COL.findall(t)) == 3 and t.count("<w:tr>") == 1]
        assert captions, f"{path.name} has no 1x3 caption table"
        for table in captions:
            assert ">)<" in table, f"{path.name}: caption has no parentheses column"


def test_every_caption_indents_only_its_minor_child_line(registry):
    """Half an inch on "A Minor Child." / "Minor Children.", nothing on the rest."""
    offenders = []
    for path in template_paths(registry):
        for table in tables(path):
            if len(GRID_COL.findall(table)) != 3 or table.count("<w:tr>") != 1:
                continue
            first_cell = table[table.index("<w:tc>"):table.index("</w:tc>")]
            for block in PARAGRAPH.findall(first_cell):
                text = paragraph_content(block).strip()
                indent = FIRST_LINE.search(block)
                wants = bool(re.match(r'(a\s+)?minor\s+child', text, re.I))
                has = bool(indent and indent.group(1) == "720")
                if wants != has:
                    offenders.append(f"{path.name}: {text[:40]!r} indent={has}, expected {wants}")
    assert not offenders, "\n".join(offenders)
