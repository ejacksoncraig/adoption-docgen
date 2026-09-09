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
# Converting the office's .doc originals left every numbered paragraph with a 1"
# first-line indent, so the number sat an inch in and its text half an inch
# further again. The originals put the number hard against the left margin and
# let one tab carry the text to the first half-inch stop. This is a whole-page
# defect rather than a wrong value, so nothing else in the suite would catch it
# coming back — a re-converted template would simply drift out to the right.

PARAGRAPH = re.compile(r'<w:p\b(?:(?!</w:p>).)*?</w:p>', re.S)
FIRST_LINE = re.compile(r'<w:ind[^>]*w:firstLine="(\d+)"[^>]*/>')

#: The notice's rights list is a sub-list under "PURSUANT TO 10 O.S. §40.4" and
#: is a level further in than the allegations above it — number at 0.5", text at
#: 1". Its original does the same, so it is allowed rather than flattened.
SUB_LIST_INDENT = "720"


def paragraph_content(block: str) -> str:
    """Text and tabs in document order. A <w:tab/> is not a <w:t>."""
    return "".join(
        "\t" if m.group(0).startswith("<w:tab") else m.group(1)
        for m in re.finditer(r'<w:tab\s*/>|<w:t(?:\s[^>]*)?>(.*?)</w:t>', block, re.S)
    )


def numbered_paragraphs(path):
    """Every "1.<tab>..." paragraph in a template, with its first-line indent."""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    for block in PARAGRAPH.findall(xml):
        text = paragraph_content(block)
        if not re.match(r"\d+\.\t", text):
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


def test_numbered_allegations_start_at_the_left_margin(registry):
    offenders = []
    for path in template_paths(registry):
        for indent, text in numbered_paragraphs(path):
            if indent not in ("0", SUB_LIST_INDENT):
                offenders.append(f"{path.name}: first-line {indent} on {text[:50]!r}")
    assert not offenders, "numbered paragraphs indented off the margin:\n" + "\n".join(offenders)


def test_every_template_has_numbered_paragraphs_to_check(registry):
    """Guards the guard: if the "1.<tab>" shape ever changes, the test above
    would pass by finding nothing rather than by finding it correct."""
    counted = {path.name: len(list(numbered_paragraphs(path)))
               for path in template_paths(registry)}
    assert all(counted.values()), f"no numbered paragraphs found in: {counted}"
