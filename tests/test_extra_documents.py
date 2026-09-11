"""The three documents an adoption sometimes needs, each behind its own question.

Concurrent jurisdiction, the juvenile records request and the filing cover
sheets are filed only when the matter calls for them, so each is gated on a bool
the intake asks — the same mechanism the Notice to Tribe uses for ICWA.

The thing worth testing hardest is the counties. A juvenile deprived case is
often not in the county the adoption is filed in, and the concurrent
jurisdiction application names a third court besides. Printing the filing county
in all three places would look entirely plausible and be wrong.
"""

from __future__ import annotations

import re
import zipfile

import pytest

from app import engine
from tests import fixtures

CONCURRENT = "dhs_concurrent_jurisdiction.docx"
JUVENILE = "dhs_juvenile_records.docx"
COVERS = "dhs_filing_covers.docx"

EXTRAS = {
    "concurrent_jurisdiction": CONCURRENT,
    "juvenile_records": JUVENILE,
    "filing_cover_sheets": COVERS,
}


def generate(registry, values, tmp_path, today, variant="dhs_1p_1c"):
    return engine.generate(registry, "dhs", variant, values, today=today, output_root=tmp_path)


def text_of(result, template):
    generated = next((f for f in result.files if f.template.endswith(template)), None)
    assert generated is not None, f"{template} not among {[f.template for f in result.files]}"
    return engine.document_text(generated.path)


# --------------------------------------------------------------------------
# asked for, or not produced at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question,template", EXTRAS.items())
def test_each_extra_is_produced_only_when_it_is_asked_for(
    registry, tmp_path, today, question, template
):
    off = generate(registry, fixtures.BASE, tmp_path / "off", today)
    assert not any(f.template.endswith(template) for f in off.files)

    on = generate(registry, fixtures.values(**{question: True}), tmp_path / "on", today)
    assert any(f.template.endswith(template) for f in on.files)


def test_an_intake_that_says_nothing_produces_none_of_them(registry, tmp_path, today):
    """They are opt-in. A saved intake from before these existed answers none of
    these questions, and must not start producing three extra documents."""
    silent = {k: v for k, v in fixtures.BASE.items() if k not in EXTRAS}
    result = generate(registry, silent, tmp_path, today)
    for template in EXTRAS.values():
        assert not any(f.template.endswith(template) for f in result.files)


@pytest.mark.parametrize("token", ["XXX", "{{", "{%"])
def test_no_placeholder_survives_into_the_extras(registry, tmp_path, today, token):
    values = fixtures.values(**{q: True for q in EXTRAS})
    result = generate(registry, values, tmp_path, today)
    for template in EXTRAS.values():
        assert token not in text_of(result, template)


# --------------------------------------------------------------------------
# three counties, and none of them interchangeable
# --------------------------------------------------------------------------


def test_the_juvenile_documents_are_headed_with_the_juvenile_county(
    registry, tmp_path, today
):
    """The deprived case's court, which is usually not where the adoption is
    filed — that is the whole reason concurrent jurisdiction is being asked for."""
    values = fixtures.values(county="Muskogee", juvenile_county="Latimer",
                             concurrent_jurisdiction=True, juvenile_records=True)
    result = generate(registry, values, tmp_path, today)

    for template in (CONCURRENT, JUVENILE):
        text = text_of(result, template)
        assert "IN THE DISTRICT COURT IN AND FOR LATIMER COUNTY" in text, template
        assert "IN AND FOR MUSKOGEE COUNTY" not in text, template


def test_the_application_names_the_court_being_asked_to_take_the_adoption(
    registry, tmp_path, today
):
    """A third county: the juvenile court is asked to release the matter *to*
    somewhere, and that somewhere is named in the body."""
    values = fixtures.values(county="Muskogee", juvenile_county="Latimer",
                             probate_county="Wagoner", concurrent_jurisdiction=True)
    text = text_of(generate(registry, values, tmp_path, today), CONCURRENT)

    assert "probate Court for WAGONER County" in text
    assert "District Court of WAGONER County" in text
    # and the heading is still the juvenile court's
    assert "IN AND FOR LATIMER COUNTY" in text


def test_both_counties_fall_back_to_the_filing_county(registry, tmp_path, today):
    """The usual case is that they are all the same, and nobody should have to
    answer the same question three times to say so."""
    values = fixtures.values(county="Wagoner", concurrent_jurisdiction=True)
    text = text_of(generate(registry, values, tmp_path, today), CONCURRENT)

    assert "IN AND FOR WAGONER COUNTY" in text
    assert "probate Court for WAGONER County" in text


def test_the_cover_sheets_follow_the_filing_county(registry, tmp_path, today):
    """They are filed with the petition, so they belong to the adoption court
    however the juvenile case is captioned."""
    values = fixtures.values(county="Muskogee", juvenile_county="Latimer",
                             filing_cover_sheets=True)
    text = text_of(generate(registry, values, tmp_path, today), COVERS)

    assert "IN THE DISTRICT COURT IN AND FOR MUSKOGEE COUNTY" in text
    assert "LATIMER" not in text


# --------------------------------------------------------------------------
# the juvenile case number
# --------------------------------------------------------------------------


def test_the_juvenile_case_number_prints_where_it_is_given(registry, tmp_path, today):
    values = fixtures.values(juvenile_case_number="JD-2022-3",
                             concurrent_jurisdiction=True, juvenile_records=True)
    result = generate(registry, values, tmp_path, today)
    for template in (CONCURRENT, JUVENILE):
        assert "JD-2022-3" in text_of(result, template)


def test_the_juvenile_case_number_prints_a_line_when_it_is_not_known(
    registry, tmp_path, today
):
    values = {k: v for k, v in fixtures.values(juvenile_records=True).items()
              if k != "juvenile_case_number"}
    assert "JD-__________" in text_of(generate(registry, values, tmp_path, today), JUVENILE)


# --------------------------------------------------------------------------
# captions, the same shape as every other document in the filing
# --------------------------------------------------------------------------


CAPTION = re.compile(r'<w:tbl>.*?</w:tbl>', re.S)


def caption_tables(path):
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return [t for t in CAPTION.findall(xml)
            if t.count("<w:gridCol") == 3 and t.count("<w:tr>") == 1]


@pytest.mark.parametrize("template,expected", [(CONCURRENT, 2), (JUVENILE, 2), (COVERS, 8)])
def test_every_page_is_captioned_the_same_way_as_the_rest(registry, template, expected):
    tables = caption_tables(registry.template_path(f"dhs/{template}"))
    assert len(tables) == expected
    for table in tables:
        assert ">)<" in table
        assert re.search(r'w:val="(single|double|dashed|dotted|thick)"', table) is None


def test_the_juvenile_caption_is_the_states_action_not_the_adoption(
    registry, tmp_path, today
):
    """A deprived case is captioned in the State's name and carries the JD
    number. Captioning it as the adoption would be the wrong case entirely."""
    values = fixtures.values(juvenile_records=True, juvenile_case_number="JD-2022-3")
    text = text_of(generate(registry, values, tmp_path, today), JUVENILE)

    assert "In the Matter of the State of Oklahoma" in text
    assert "An alleged DEPRIVED" in text
    assert "In the Matter of the Adoption of" not in text
    assert "FA-" not in text


def test_a_second_child_reaches_the_juvenile_caption(registry, tmp_path, today):
    values = {
        **fixtures.BASE,
        "juvenile_records": True,
        "child2_name": "SAMPLE CHILD TWO",
        "child2_dob": "2016-02-15",
        "child2_race": "Caucasian",
        "child2_gender": "female",
        "child2_birth_place": "Sample Regional Hospital",
        "child2_birth_city": "Sampletown",
        "child2_birth_county": "Sample",
        "child2_birth_state": "Oklahoma",
        "name_change_child2": False,
    }
    text = text_of(generate(registry, values, tmp_path, today, variant="dhs_1p_2c"), JUVENILE)
    assert "SAMPLE CHILD TWO" in text
