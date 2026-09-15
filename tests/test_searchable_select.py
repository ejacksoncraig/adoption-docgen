"""county: a select field with 77 options, and what that implies elsewhere.

A native <select> with 77 entries is workable but slow to scroll, so the
intake form renders it as a hand-built searchable box instead — see
renderSearchableSelect in app/ui/index.html. None of that widget choice
changes what a valid answer is: county is still a plain select as far as
Python is concerned, checked against the same option list either way.

The one other place the option count matters is the paper questionnaire,
which used to spell out every choice on one printed line ("Adair  /
Alfalfa  /  Atoka  /  ..."). Unreadable at 10 options and worse at 77.
"""

from __future__ import annotations

from app.intake import _answer_hint
from app.registry import Registry
from app.schema import FieldDef, Schema


def test_every_oklahoma_county_is_present_once():
    registry = Registry.load()
    options = registry.schema.fields["county"].options
    assert len(options) == 77
    assert len(set(options)) == 77                    # no duplicate entered twice
    assert options == tuple(sorted(options))           # alphabetical, so it reads as a list


def test_the_county_field_is_still_an_ordinary_select():
    """The searchable widget is a UI choice. Python does not know or care."""
    registry = Registry.load()
    fd = registry.schema.fields["county"]
    assert fd.type == "select"
    assert fd.searchable is True


def test_searchable_defaults_to_false():
    schema = Schema({
        "groups": {"case": {"label": "Case", "order": 1}},
        "fields": [{"id": "x", "group": "case", "type": "select", "options": ["a", "b"]}],
    })
    assert schema.fields["x"].searchable is False


# --------------------------------------------------------------------------
# what reaches the intake form
# --------------------------------------------------------------------------


def test_form_spec_tells_the_ui_which_selects_are_searchable():
    registry = Registry.load()
    spec = registry.form_spec("dhs", "dhs_1p_1c")
    fields = {f["id"]: f for group in spec["groups"] for f in group["fields"]}
    assert fields["county"]["searchable"] is True
    assert len(fields["county"]["options"]) == 77
    # an ordinary select on the same form is not marked searchable
    assert fields["petitioner1_gender"]["searchable"] is False


# --------------------------------------------------------------------------
# the paper questionnaire, which cannot filter as someone types
# --------------------------------------------------------------------------


def test_a_searchable_select_gets_a_blank_line_on_paper():
    """77 counties spelled out on one line is not a question a person can use.
    A rule to write the answer along is what the office already relies on for
    open-ended text fields."""
    fd = FieldDef(id="county", group="case", type="select", label="Filing county",
                 options=("Adair", "Alfalfa"), searchable=True)
    assert _answer_hint(fd) == ""          # nothing printed; the cell is ruled instead


def test_a_short_select_still_lists_its_choices():
    """The blank line is specifically for a list too long to read at a glance —
    an ordinary short select keeps showing its options, which is more useful
    than a blank line when there are only two or three of them."""
    fd = FieldDef(id="petitioner1_gender", group="petitioner1", type="select", label="Gender",
                 options=("male", "female"), searchable=False)
    assert _answer_hint(fd) == "Male  /  Female"
