"""The Notice to Tribe: generated only when ICWA applies, addressed to whichever
tribe was named, for either matter and either child count."""

from __future__ import annotations

import pytest

from app import engine
from tests import fixtures
from tests.test_render import render

NOTICE = "notice_to_tribe.docx"

KNOWN_TRIBES = {
    "Cherokee Nation": ("ICW Adoptions", "P.O. Box 948", "Tahlequah, OK 74465"),
    "Chickasaw Nation": ("Adoption Unit", "1401 Hoppe Blvd", "Ada, OK 74820"),
    "Choctaw Nation of Oklahoma": ("ICW Adoptions", "P.O. Box 1210", "Durant, OK 74702-1210"),
    "Muscogee (Creek) Nation": ("Children and Family Services", "P.O. Box 580", "Okmulgee, OK 74447"),
    "Citizen Potawatomi Nation": ("ICW / Adoptions", "1601 S. Gordon Cooper Drive", "Shawnee, OK 74801"),
}


# --------------------------------------------------------------------------
# generated only when ICWA applies
# --------------------------------------------------------------------------


def test_notice_is_generated_when_icwa_applies(registry, tmp_path, today):
    result = engine.generate(registry, "dhs", "dhs_1p_1c", fixtures.BASE, today=today, output_root=tmp_path)
    assert any(f.template.endswith(NOTICE) for f in result.files)


def test_notice_is_skipped_when_icwa_does_not_apply(registry, tmp_path, today):
    result = engine.generate(registry, "dhs", "dhs_1p_1c", fixtures.ALTERNATE, today=today, output_root=tmp_path)
    assert not any(f.template.endswith(NOTICE) for f in result.files)


def test_notice_is_generated_for_a_stepparent_filing_when_icwa_applies(registry, tmp_path, today):
    values = fixtures.stepparent(icwa_applies=True, tribe="Cherokee Nation")
    result = engine.generate(registry, "stepparent", "step_1c", values, today=today, output_root=tmp_path)
    assert any(f.template.endswith(NOTICE) for f in result.files)


def test_notice_is_skipped_for_a_stepparent_filing_when_icwa_does_not_apply(registry, tmp_path, today):
    result = engine.generate(registry, "stepparent", "step_1c", fixtures.STEPPARENT, today=today, output_root=tmp_path)
    assert not any(f.template.endswith(NOTICE) for f in result.files)


# --------------------------------------------------------------------------
# no leftover placeholders, whichever branch
# --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["XXX", "{{", "{%"])
def test_no_placeholder_survives_into_the_notice(registry, tmp_path, today, token):
    text = render(registry, fixtures.BASE, tmp_path, today, template=NOTICE)
    assert token not in text


# --------------------------------------------------------------------------
# the correct tribe's office, addressed automatically
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tribe,expected", KNOWN_TRIBES.items())
def test_each_known_tribe_gets_its_own_office_address(registry, tmp_path, today, tribe, expected):
    text = render(registry, fixtures.values(tribe=tribe), tmp_path, today, template=NOTICE)
    assert tribe in text
    for line in expected:
        assert line in text
    other_addresses = [addr for name, addr in KNOWN_TRIBES.items() if name != tribe]
    for _, _, city in other_addresses:
        assert city not in text


def test_a_tribe_outside_the_known_list_still_generates_a_notice(registry, tmp_path, today):
    """The office told us to expect more tribes than these five. A name that
    doesn't match one of them still produces a notice — it just says so, rather
    than printing an address that belongs to a different tribe."""
    text = render(registry, fixtures.values(tribe="Osage Nation"), tmp_path, today, template=NOTICE)
    assert "Osage Nation" in text
    assert "Tribe mailing address not on file" in text
    for _, _, city in KNOWN_TRIBES.values():
        assert city not in text


# --------------------------------------------------------------------------
# one child or several, in the caption
# --------------------------------------------------------------------------


def test_single_child_caption_reads_in_the_singular(registry, tmp_path, today):
    text = render(registry, fixtures.BASE, tmp_path, today, template=NOTICE)
    assert "A Minor Child." in text
    assert "Minor Children." not in text
    assert "is a member of the Sample Nation" in text
    assert "And" not in text.split("NOTICE TO TRIBE")[0]


def test_two_children_share_one_notice_in_the_plural(registry, tmp_path, today):
    values = {
        **fixtures.BASE,
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
    text = render(registry, values, tmp_path, today, template=NOTICE, variant="dhs_1p_2c")
    assert "Minor Children." in text
    assert "A Minor Child." not in text
    assert "SAMPLE CHILD" in text and "SAMPLE CHILD TWO" in text
    assert "are members of the Sample Nation" in text
