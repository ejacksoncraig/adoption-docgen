"""Step-parent adoptions: derived pronouns and the three-way consent branch.

¶13 of the step-parent petition is the paragraph most likely to go wrong without
anyone noticing, because every branch of it is grammatical. "he consent is not
necessary" reads as a typo; "she consents" under a paragraph about a father reads
as correct English about the wrong person.
"""

from __future__ import annotations

import pytest

from app import engine
from app.schema import IntakeError
from tests import fixtures
from tests.test_render import render

MATTER, VARIANT = "stepparent", "step_1c"


def para13(text: str) -> str:
    return next(line.strip() for line in text.splitlines() if line.strip().startswith("13."))


def step_text(registry, tmp_path, today, **overrides) -> str:
    return render(
        registry, fixtures.stepparent(**overrides), tmp_path, today,
        template="step_petition_1c.docx", matter=MATTER, variant=VARIANT,
    )


# --------------------------------------------------------------------------
# pronouns
# --------------------------------------------------------------------------


def test_pronouns_follow_the_parent(schema):
    ctx = schema.build_context(
        fixtures.stepparent(other_parent_relationship="mother"),
        ["case", "petitioner1", "petitioner2", "child1", "stepparent", "icwa"], {}, None,
    )
    assert ctx["other_parent_pronoun"] == "she"
    assert ctx["other_parent_pronoun_object"] == "her"
    assert ctx["other_parent_pronoun_possessive"] == "her"


def test_pronouns_follow_a_father(schema):
    ctx = schema.build_context(
        fixtures.stepparent(other_parent_relationship="father"),
        ["case", "petitioner1", "petitioner2", "child1", "stepparent", "icwa"], {}, None,
    )
    assert (ctx["other_parent_pronoun"], ctx["other_parent_pronoun_object"],
            ctx["other_parent_pronoun_possessive"]) == ("he", "him", "his")


def test_a_value_with_no_known_pronoun_stops_generation(schema):
    from app.schema import _pronoun

    with pytest.raises(IntakeError):
        _pronoun("guardian", None)


# --------------------------------------------------------------------------
# all three consent bases, both parents
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relationship, subject, possessive", [("mother", "she", "her"), ("father", "he", "his")])
def test_other_parent_consents(registry, tmp_path, today, relationship, subject, possessive):
    text = para13(step_text(registry, tmp_path, today,
                            other_parent_consent_basis="consents", other_parent_relationship=relationship))
    assert f"the natural {relationship} of" in text
    assert f"and {subject} consents to this adoption" in text


@pytest.mark.parametrize("relationship, possessive", [("mother", "her"), ("father", "his")])
def test_other_parent_rights_already_terminated(registry, tmp_path, today, relationship, possessive):
    text = para13(step_text(registry, tmp_path, today,
                            other_parent_consent_basis="terminated", other_parent_relationship=relationship))
    assert f"and {possessive} parental rights were previously terminated" in text
    assert "'s parental rights" not in text  # would read "he's parental rights"


@pytest.mark.parametrize("relationship, subject, possessive",
                         [("mother", "she", "her"), ("father", "he", "his")])
def test_consent_excused_for_non_support(registry, tmp_path, today, relationship, subject, possessive):
    text = para13(step_text(registry, tmp_path, today,
                            other_parent_consent_basis="excused_nonsupport",
                            other_parent_relationship=relationship))
    assert f"and {possessive} consent is not necessary" in text
    assert f"because {subject} has willfully failed" in text


def test_the_three_branches_are_actually_different(registry, tmp_path, today):
    texts = {
        basis: para13(step_text(registry, tmp_path, today, other_parent_consent_basis=basis))
        for basis in ("consents", "terminated", "excused_nonsupport")
    }
    assert len(set(texts.values())) == 3


# --------------------------------------------------------------------------
# the rest of the step-parent filing
# --------------------------------------------------------------------------


def test_a_step_parent_filing_generates_every_document(registry, tmp_path, today):
    result = engine.generate(registry, MATTER, VARIANT, fixtures.STEPPARENT,
                             today=today, output_root=tmp_path)
    names = sorted(f.path.name for f in result.files)
    assert names == ["1 Step-Parent Petition - SAMPLE CHILD.docx",
                     "2 Filing Packet - SAMPLE CHILD.docx"]


@pytest.mark.parametrize("token", ["XXX", "{{", "{%"])
def test_no_placeholder_survives(registry, tmp_path, today, token):
    for basis in ("consents", "terminated", "excused_nonsupport"):
        assert token not in step_text(registry, tmp_path, today, other_parent_consent_basis=basis)


def test_the_marriage_date_reads_as_prose(registry, tmp_path, today):
    text = step_text(registry, tmp_path, today)
    assert "11th day of October, 2014" in text
