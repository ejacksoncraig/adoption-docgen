"""Exporting an intake that is not finished.

Staff need to take a half-answered petition away and work on it. The brief is
equally clear that a document rendering with a blank where a name belongs is worse
than one that refuses to render at all — a blank looks finished.

So an unanswered question is allowed through, but never as a blank: it prints as a
marker naming the question, and every file is named DRAFT. These tests hold that
line, because it is the whole reason partial export is safe.
"""

from __future__ import annotations

import pytest

from app import engine
from app.schema import IntakeError
from conftest import PILOT_MATTER, PILOT_VARIANT
from tests import fixtures

#: Enough to identify the matter, nowhere near enough to file.
STARTED = {
    "county": "Wagoner",
    "petitioner1_name": "SAMPLE PETITIONER",
    "child1_name": "SAMPLE CHILD",
    "name_change": False,
    "icwa_applies": False,
    "bio_mother_status": "relinquished",
    "bio_father_status": "terminated",
}


@pytest.fixture
def draft(registry, tmp_path, today):
    return engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, STARTED,
                           today=today, output_root=tmp_path, draft=True)


@pytest.fixture
def draft_text(draft):
    petition = next(f for f in draft.files if "Petition and Decree" in f.path.name)
    return engine.document_text(petition.path)


# --------------------------------------------------------------------------
# it works at all
# --------------------------------------------------------------------------


def test_a_part_finished_intake_still_produces_documents(draft, registry):
    expected = len(registry.variant(PILOT_MATTER, PILOT_VARIANT).all_documents())
    assert len(draft.files) == expected
    assert all(f.path.exists() for f in draft.files)


def test_the_answers_that_were_given_are_used(draft_text):
    assert "SAMPLE CHILD" in draft_text
    assert "SAMPLE PETITIONER" in draft_text
    assert "IN AND FOR WAGONER COUNTY" in draft_text


def test_conditionals_still_follow_the_answers_that_were_given(draft_text):
    """ICWA was answered No and the name change No; those paragraphs must read
    that way rather than becoming markers."""
    assert "not of Indian blood" in draft_text
    assert "does not desire a name change" in draft_text


# --------------------------------------------------------------------------
# a gap is never a blank
# --------------------------------------------------------------------------


def test_an_unanswered_question_prints_as_a_marker(draft_text):
    assert "[ Child — Race ]" in draft_text
    assert "[ Child — Hospital or place of birth ]" in draft_text


def test_a_marker_names_the_question_a_person_can_answer(draft_text):
    """child1_birth_day is derived from the date of birth. Telling staff to fill
    in "child1_birth_day" would be describing plumbing, not asking a question."""
    assert "[ Child — Date of birth ]" in draft_text
    assert "child1_birth_day" not in draft_text
    assert "child1_dob" not in draft_text


def test_no_gap_is_left_empty(draft_text, registry):
    """The failure this whole design exists to prevent: an empty space where a
    name belongs, in a document that otherwise looks ready to file."""
    for phrase in ("is a  ", "at , in the City of", "County, State of ."):
        assert phrase not in draft_text


def test_a_draft_still_carries_no_template_tags(draft):
    for generated in draft.files:
        assert engine.find_leftovers(engine.document_text(generated.path)) == []


# --------------------------------------------------------------------------
# it cannot be mistaken for a finished filing
# --------------------------------------------------------------------------


def test_every_file_is_named_draft(draft):
    assert all(f.path.name.startswith(engine.DRAFT_PREFIX) for f in draft.files)


def test_the_folder_is_named_draft(draft):
    assert draft.folder.name.startswith("DRAFT_")


def test_the_result_knows_it_is_a_draft(draft):
    assert draft.is_draft
    assert draft.as_dict()["draft"] is True


def test_the_gaps_are_reported_for_staff_to_work_through(draft):
    assert draft.gaps
    assert "Child — Date of birth" in draft.gaps
    assert draft.gaps == sorted(set(draft.gaps)), "gaps should be deduplicated and ordered"


def test_a_question_that_does_not_apply_is_not_called_a_gap(draft):
    """ICWA was answered No, so the tribe is not missing — it does not apply."""
    assert not any("Tribe" in gap for gap in draft.gaps)


# --------------------------------------------------------------------------
# a finished intake is unaffected
# --------------------------------------------------------------------------


def test_a_complete_intake_is_not_marked_as_a_draft(registry, tmp_path, today):
    result = engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, fixtures.BASE,
                             today=today, output_root=tmp_path, draft=True)
    assert result.gaps == []
    assert not result.is_draft
    assert not any(f.path.name.startswith(engine.DRAFT_PREFIX) for f in result.files)


def test_drafting_does_not_change_a_finished_document(registry, tmp_path, today):
    strict = engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, fixtures.BASE,
                             today=today, output_root=tmp_path / "strict")
    lenient = engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, fixtures.BASE,
                              today=today, output_root=tmp_path / "lenient", draft=True)
    assert engine.document_text(strict.files[0].path) == engine.document_text(lenient.files[0].path)


# --------------------------------------------------------------------------
# absent is forgiven; wrong is not
# --------------------------------------------------------------------------


def test_an_answer_that_is_wrong_still_stops_a_draft(registry, tmp_path, today):
    """A date that is not a date would put nonsense into the document, which is
    not the same as leaving a visible gap."""
    with pytest.raises(IntakeError):
        engine.generate(registry, PILOT_MATTER, PILOT_VARIANT,
                        {**STARTED, "child1_dob": "sometime in spring"},
                        today=today, output_root=tmp_path, draft=True)


def test_an_option_that_does_not_exist_still_stops_a_draft(registry, tmp_path, today):
    with pytest.raises(IntakeError):
        engine.generate(registry, PILOT_MATTER, PILOT_VARIANT,
                        {**STARTED, "county": "Atlantis"},
                        today=today, output_root=tmp_path, draft=True)


def test_without_draft_an_unfinished_intake_still_refuses(registry, tmp_path, today):
    """The strict path is unchanged; drafting is a deliberate choice."""
    with pytest.raises(IntakeError):
        engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, STARTED,
                        today=today, output_root=tmp_path)


# --------------------------------------------------------------------------
# every variant, not just the pilot
# --------------------------------------------------------------------------


def test_every_variant_can_be_drafted_from_almost_nothing(registry, tmp_path, today):
    """The most extreme case: a matter chosen and nothing else answered."""
    failures = []
    for matter in registry.matters:
        for variant in matter.variants:
            if not variant.is_ready:
                continue
            try:
                result = engine.generate(registry, matter.id, variant.id, {},
                                         today=today, output_root=tmp_path / variant.id, draft=True)
            except Exception as exc:  # noqa: BLE001 - collect them all
                failures.append(f"{variant.id}: {exc}")
                continue

            for generated in result.files:
                text = engine.document_text(generated.path)
                if engine.find_leftovers(text):
                    failures.append(f"{variant.id}/{generated.path.name}: leftover tags")
            if not result.is_draft:
                failures.append(f"{variant.id}: an empty intake was not marked a draft")

    assert not failures, "\n".join(failures)
