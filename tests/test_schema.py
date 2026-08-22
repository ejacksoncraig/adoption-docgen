"""Derived values: the things a human must never have to type twice."""

from __future__ import annotations

from datetime import date

import pytest

from app.schema import (
    ConfigError,
    IntakeError,
    Schema,
    format_date,
    ordinal,
    parse_date,
    years_between,
)
from tests import fixtures


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n, expected",
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
     (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (30, "30th"), (31, "31st")],
)
def test_ordinal(n, expected):
    assert ordinal(n) == expected


def test_date_formats():
    d = date(2024, 3, 9)
    assert format_date(d, "mdy") == "3/9/2024"
    assert format_date(d, "long") == "March 9, 2024"
    assert format_date(d, "long_ordinal") == "9th day of March, 2024"
    assert format_date(d, "iso") == "2024-03-09"


def test_age_is_birthday_aware():
    born = date(2000, 6, 15)
    assert years_between(born, date(2026, 6, 14)) == 25
    assert years_between(born, date(2026, 6, 15)) == 26


def test_parse_date_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_date("sometime last spring")


# --------------------------------------------------------------------------
# derived fields
# --------------------------------------------------------------------------


def test_derived_values(schema, pilot_groups, today):
    ctx = schema.build_context(fixtures.BASE, pilot_groups, {"attorney_short_name": "X"}, today)

    assert ctx["county_upper"] == "WAGONER"
    assert ctx["child1_name_upper"] == "SAMPLE CHILD"
    assert ctx["child1_dob"] == "3/2/2015"
    assert (ctx["child1_birth_day"], ctx["child1_birth_month"], ctx["child1_birth_year"]) == ("2nd", "March", "2015")
    assert ctx["petitioner1_age"] == 41
    assert ctx["child1_under_12"] is True
    assert ctx["case_year"] == "2026"
    assert ctx["foster_placement_date"] == "21st day of September, 2024"


def test_under_12_flips_with_the_date_of_birth(schema, pilot_groups, today):
    ctx = schema.build_context(fixtures.values(child1_dob="2010-01-01"), pilot_groups, {}, today)
    assert ctx["child1_under_12"] is False


def test_case_number_default_uses_the_current_year(schema, pilot_groups, today):
    ctx = schema.build_context(fixtures.BASE, pilot_groups, {}, today)
    assert ctx["case_number"] == "FA-2026-_____"

    given = schema.build_context(fixtures.values(case_number="FA-2026-42"), pilot_groups, {}, today)
    assert given["case_number"] == "FA-2026-42"


def test_settings_reach_the_context_but_the_intake_form_does_not_ask_for_them(schema, pilot_groups, today):
    ctx = schema.build_context(fixtures.BASE, pilot_groups, {"attorney_short_name": "A. Lawyer"}, today)
    assert ctx["attorney_short_name"] == "A. Lawyer"
    assert "attorney_short_name" not in {fd.id for fd in schema.input_fields(pilot_groups)}


# --------------------------------------------------------------------------
# gated fields
# --------------------------------------------------------------------------


def test_a_gated_field_is_absent_rather_than_blank(schema, pilot_groups, today):
    """tribe must not appear as an empty string — a template that used it outside
    its {% if %} has to fail, not print nothing where a tribe name belongs."""
    ctx = schema.build_context(fixtures.ALTERNATE, pilot_groups, {}, today)
    assert "tribe" not in ctx
    assert "child1_new_name" not in ctx
    assert ctx["icwa_applies"] is False


def test_a_gated_field_is_not_required_when_its_gate_is_closed(schema, pilot_groups):
    assert schema.missing_required(fixtures.ALTERNATE, pilot_groups) == []


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_missing_required_answers_are_named_with_their_group(schema, pilot_groups):
    values = fixtures.values(child1_name=None, petitioner1_name=None)
    missing = schema.missing_required(values, pilot_groups)
    assert "Child — Current legal name" in missing
    assert "Petitioner — Full legal name" in missing


def test_bad_answers_are_reported(schema, pilot_groups):
    problems = schema.validate(fixtures.values(county="Atlantis", child1_dob="whenever"), pilot_groups)
    assert any("Atlantis" in p for p in problems)
    assert any("Child — Date of birth" in p for p in problems)


def test_unknown_field_is_reported(schema, pilot_groups):
    assert any("made_up_field" in p for p in schema.validate({"made_up_field": "x"}, pilot_groups))


def test_build_context_refuses_incomplete_intake(schema, pilot_groups, today):
    with pytest.raises(IntakeError) as exc:
        schema.build_context(fixtures.values(child1_name=None), pilot_groups, {}, today)
    assert any("Current legal name" in p for p in exc.value.problems)


# --------------------------------------------------------------------------
# config errors
# --------------------------------------------------------------------------


def test_derived_field_with_no_rule_is_rejected():
    with pytest.raises(ConfigError) as exc:
        Schema({
            "groups": {"case": {"label": "Case", "order": 1}},
            "fields": [{"id": "child1_favourite_colour", "group": "case", "type": "text", "derived": True}],
        })
    assert any("no rule in schema.py knows how to compute it" in p for p in exc.value.problems)


def test_derived_field_whose_source_is_missing_is_rejected():
    with pytest.raises(ConfigError) as exc:
        Schema({
            "groups": {"child1": {"label": "Child", "order": 1}},
            "fields": [{"id": "child1_age", "group": "child1", "type": "number", "derived": True}],
        })
    assert any("derived from child1_dob" in p for p in exc.value.problems)


def test_every_config_problem_is_reported_at_once():
    with pytest.raises(ConfigError) as exc:
        Schema({
            "groups": {"case": {"label": "Case", "order": 1}},
            "fields": [
                {"id": "a", "group": "nowhere", "type": "text"},
                {"id": "b", "group": "case", "type": "colour"},
                {"id": "c", "group": "case", "type": "text", "depends_on": "nonexistent"},
            ],
        })
    assert len(exc.value.problems) == 3
