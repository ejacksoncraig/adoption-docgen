"""The "fill with test data" button.

Its job is to make showing and checking the program fast: one click gives a whole
matter that generates a finished filing. Two things have to stay true — it must
always produce a *valid* intake, or the button is worse than useless, and every
name must stay obviously fake, because what comes out is otherwise
indistinguishable from a real filing sitting in the output folder.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from app import engine, intake
from app.schema import parse_date
from conftest import PILOT_MATTER, PILOT_VARIANT
from tests import fixtures


def ready(registry):
    return [(m.id, v.id) for m in registry.matters for v in m.variants if v.is_ready]


# --------------------------------------------------------------------------
# it must always be usable
# --------------------------------------------------------------------------


def test_every_variant_gets_a_complete_valid_intake(registry):
    for matter_id, variant_id in ready(registry):
        groups = registry.variant(matter_id, variant_id).field_groups
        values = intake.random_values(registry.schema, groups)
        assert registry.schema.missing_required(values, groups) == [], variant_id
        assert registry.schema.validate(values, groups) == [], variant_id


def test_it_stays_valid_over_many_runs(registry, pilot_groups):
    """Randomness that is right most of the time is a button that fails in front
    of someone."""
    for _ in range(60):
        values = intake.random_values(registry.schema, pilot_groups)
        assert registry.schema.missing_required(values, pilot_groups) == []
        assert registry.schema.validate(values, pilot_groups) == []


def test_it_generates_a_finished_filing_not_a_draft(registry, tmp_path, today, pilot_groups):
    values = intake.random_values(registry.schema, pilot_groups)
    result = engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, values,
                             today=today, output_root=tmp_path, draft=True)
    assert not result.is_draft
    assert result.gaps == []
    for generated in result.files:
        assert engine.find_leftovers(engine.document_text(generated.path)) == []


# --------------------------------------------------------------------------
# it must be obviously fake
# --------------------------------------------------------------------------


def test_every_name_is_marked_as_a_sample(registry, pilot_groups):
    values = intake.random_values(registry.schema, pilot_groups)
    for field_id, value in values.items():
        if field_id.endswith("_name"):
            assert str(value).startswith("SAMPLE "), f"{field_id} = {value!r}"


def test_a_generated_document_says_sample_throughout(registry, tmp_path, today, pilot_groups):
    """Whoever finds this in the output folder must see instantly that it is not
    a real matter."""
    values = intake.random_values(registry.schema, pilot_groups)
    result = engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, values,
                             today=today, output_root=tmp_path)
    text = engine.document_text(result.files[0].path)
    assert text.count("SAMPLE") >= 5


# --------------------------------------------------------------------------
# it must actually vary
# --------------------------------------------------------------------------


def test_two_presses_give_two_different_matters(registry, pilot_groups):
    first = intake.random_values(registry.schema, pilot_groups)
    second = intake.random_values(registry.schema, pilot_groups)
    assert first != second


def test_it_exercises_both_sides_of_the_conditionals(registry, pilot_groups):
    """The point of varying it: successive runs should read different paragraphs,
    not the same one with different names."""
    seen = set()
    for _ in range(60):
        values = intake.random_values(registry.schema, pilot_groups)
        seen.add((values["icwa_applies"], values["name_change"],
                  values["bio_mother_status"], values["bio_father_status"]))
    assert len(seen) >= 8, f"only {len(seen)} combinations in 60 runs"


def test_a_seed_makes_it_repeatable(registry, pilot_groups):
    """So a run that produced something odd can be reproduced."""
    assert (intake.random_values(registry.schema, pilot_groups, seed=7)
            == intake.random_values(registry.schema, pilot_groups, seed=7))


def test_the_select_answers_are_not_stuck_on_one_option(registry, pilot_groups):
    counties = {intake.random_values(registry.schema, pilot_groups)["county"] for _ in range(60)}
    assert len(counties) >= 4, f"only saw {counties}"


# --------------------------------------------------------------------------
# the answers have to make sense together
# --------------------------------------------------------------------------


def test_children_are_children_and_adults_are_adults(registry, pilot_groups):
    for _ in range(25):
        values = intake.random_values(registry.schema, pilot_groups)
        child = parse_date(values["child1_dob"])
        adult = parse_date(values["petitioner1_dob"])
        assert child > adult, "the child must be younger than the petitioner"
        assert (date.today() - child).days // 365 <= 17
        assert (date.today() - adult).days // 365 >= 24


def test_dates_are_in_the_past(registry, pilot_groups):
    values = intake.random_values(registry.schema, pilot_groups)
    for field_id, value in values.items():
        if registry.schema.fields[field_id].type == "date":
            assert parse_date(value) < date.today(), field_id


def test_the_fee_summary_adds_up(registry, pilot_groups):
    """The affidavit is sworn to, so its total has to be its parts added up.

    It is computed rather than typed for exactly this reason — the figures and
    the total used to be one free-text box, where they could drift apart with
    nothing to notice."""
    values = intake.random_values(registry.schema, pilot_groups)
    context = registry.schema.build_context(values, pilot_groups, {}, date.today())
    summary = context["expenditures_summary"]

    hours = float(re.search(r"Total Hours = ([\d,.]+) x", summary).group(1).replace(",", ""))
    rate = float(re.search(r"Hourly Rate = \$([\d,.]+)", summary).group(1).replace(",", ""))
    fees = float(re.search(r"x \$[\d,.]+ = \$([\d,.]+);", summary).group(1).replace(",", ""))
    filing = float(re.search(r"Filing Fee = \$([\d,.]+);", summary).group(1).replace(",", ""))
    certificate = float(re.search(r"Amended Birth Certificate = \$([\d,.]+);", summary).group(1).replace(",", ""))
    # anchored on the cents, so the sentence's full stop is not read as part of it
    total = float(re.search(r"Total = \$([\d,]+\.\d{2})", summary).group(1).replace(",", ""))

    assert hours == values["attorney_hours"]
    assert rate == values["attorney_hourly_rate"]
    assert fees == pytest.approx(hours * rate)
    assert total == pytest.approx(fees + filing + certificate)


def test_a_fee_of_nothing_is_left_out_of_the_affidavit(registry, pilot_groups):
    """A line reading "Amended Birth Certificate = $0.00" is not a cost, it is
    an absence, and it should not be sworn to as one."""
    values = {**fixtures.BASE, "amended_certificate_fee": 0, "filing_fee": 0}
    summary = registry.schema.build_context(
        values, pilot_groups, {}, date.today())["expenditures_summary"]

    assert "Amended Birth Certificate" not in summary
    assert "Filing Fee" not in summary
    assert summary.endswith("Total = $3,900.00.")


def test_the_typed_summary_overrides_the_computed_one(registry, pilot_groups):
    """Left blank, the sentence is built. Filled in, it is printed verbatim —
    the escape hatch for a matter the four figures cannot describe."""
    values = {**fixtures.BASE, "attorney_fees_summary": "Waived in full."}
    context = registry.schema.build_context(values, pilot_groups, {}, date.today())
    assert context["attorney_fees_summary"] == "Waived in full."


# --------------------------------------------------------------------------
# questions that were never asked have no answers
# --------------------------------------------------------------------------


def test_a_gated_question_is_left_out_when_its_gate_is_shut(registry, pilot_groups):
    for _ in range(40):
        values = intake.random_values(registry.schema, pilot_groups)
        if not values["icwa_applies"]:
            assert "tribe" not in values
        if not values["name_change"]:
            assert "child1_new_name" not in values


def test_a_field_with_a_default_is_left_blank(registry, pilot_groups):
    """case_number has a default of FA-<year>-_____; filling it would mean the
    default never gets exercised."""
    values = intake.random_values(registry.schema, pilot_groups)
    assert "case_number" not in values


# --------------------------------------------------------------------------
# through the bridge
# --------------------------------------------------------------------------


def test_the_button_reaches_it(registry):
    from app.main import Api

    res = Api(registry).random_intake({"matter": PILOT_MATTER, "variant": PILOT_VARIANT})
    assert res["ok"]
    assert res["count"] == len(res["values"])
    assert res["values"]["child1_name"].startswith("SAMPLE ")


def test_the_button_reports_a_bad_variant_readably(registry):
    from app.main import Api

    res = Api(registry).random_intake({"matter": "dhs", "variant": "nope"})
    assert res["ok"] is False
    assert res["problems"]
