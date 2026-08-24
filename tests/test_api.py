"""The JS bridge.

Every method must answer with a dict the front end can display. A failure has to
arrive as ``{"ok": false, "problems": [...]}`` — never as an exception that kills
the bridge and leaves the window sitting there looking like it is thinking.
"""

from __future__ import annotations

import pytest

from app.main import Api
from conftest import PILOT_MATTER, PILOT_VARIANT
from tests import fixtures


@pytest.fixture
def api(registry) -> Api:
    return Api(registry)


def test_bootstrap_describes_the_installation(api):
    res = api.bootstrap()
    assert res["ok"]
    assert res["catalog"]
    assert isinstance(res["pdf_available"], bool)
    assert res["output_dir"]


def test_form_spec_is_everything_the_form_needs(api):
    res = api.form_spec({"matter": PILOT_MATTER, "variant": PILOT_VARIANT})
    assert res["ok"]
    groups = res["spec"]["groups"]
    assert [g["id"] for g in groups] == ["case", "petitioner1", "child1", "bio_parents", "dhs", "icwa"]
    county = next(f for g in groups for f in g["fields"] if f["id"] == "county")
    assert county["type"] == "select" and "Wagoner" in county["options"] and county["required"]
    tribe = next(f for g in groups for f in g["fields"] if f["id"] == "tribe")
    assert tribe["depends_on"] == "icwa_applies"


def test_review_of_an_empty_form_lists_what_is_missing(api):
    res = api.review({"matter": PILOT_MATTER, "variant": PILOT_VARIANT, "values": {}})
    assert res["ok"]
    assert res["problems"] == []          # nothing is *wrong* yet, only unanswered
    assert "Child — Current legal name" in res["missing"]


def test_review_of_a_complete_form_shows_the_resolved_file_names(api):
    res = api.review({"matter": PILOT_MATTER, "variant": PILOT_VARIANT, "values": fixtures.BASE})
    assert res["ok"] and res["missing"] == [] and res["problems"] == []
    assert "Petition and Decree - SAMPLE CHILD.docx" in res["documents"]
    # the matter's common documents are listed too, so staff see the whole filing
    assert len(res["documents"]) == 3


def test_review_separates_wrong_answers_from_absent_ones(api):
    res = api.review({
        "matter": PILOT_MATTER, "variant": PILOT_VARIANT,
        "values": fixtures.values(county="Atlantis", child1_name=None),
    })
    assert any("Atlantis" in p for p in res["problems"])
    assert "Child — Current legal name" in res["missing"]


def test_generate_writes_documents(api, tmp_path, monkeypatch):
    monkeypatch.setattr("app.engine.OUTPUT_DIR", tmp_path)
    res = api.generate({"matter": PILOT_MATTER, "variant": PILOT_VARIANT, "values": fixtures.BASE})
    assert res["ok"]
    assert res["result"]["files"][0]["name"] == "Petition and Decree - SAMPLE CHILD.docx"
    assert (tmp_path / res["result"]["folder"].split("\\")[-1]).exists()


def test_an_empty_intake_produces_a_draft_rather_than_refusing(api, tmp_path, monkeypatch):
    """Staff can take an unfinished petition away to work on. What they cannot do
    is end up with something that looks finished and is not."""
    monkeypatch.setattr("app.engine.OUTPUT_DIR", tmp_path)
    res = api.generate({"matter": PILOT_MATTER, "variant": PILOT_VARIANT, "values": {}})

    assert res["ok"] is True
    assert res["result"]["draft"] is True
    assert res["result"]["gaps"], "a draft must say what is still unanswered"
    assert all(f["name"].startswith("DRAFT - ") for f in res["result"]["files"])


def test_a_wrong_answer_still_refuses(api):
    """Absent is forgiven; wrong is not. A date that is not a date would put
    nonsense in a filing rather than a visible gap."""
    res = api.generate({
        "matter": PILOT_MATTER, "variant": PILOT_VARIANT,
        "values": {**fixtures.BASE, "child1_dob": "whenever"},
    })
    assert res["ok"] is False
    assert any("not a date" in problem for problem in res["problems"])


@pytest.mark.parametrize(
    "method, payload",
    [
        ("form_spec", {"matter": "nope", "variant": "nope"}),
        ("review", {"matter": "dhs", "variant": "nope", "values": {}}),
        ("generate", {"matter": "nope", "variant": "nope", "values": {}}),
        ("load_intake", {"path": "does-not-exist.json"}),
    ],
)
def test_bad_requests_come_back_as_readable_problems(api, method, payload):
    res = getattr(api, method)(payload)
    assert res["ok"] is False
    assert res["problems"] and all(isinstance(p, str) and p for p in res["problems"])


def test_an_unexpected_error_is_still_a_dict(api, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(api.registry, "form_spec", boom)
    res = api.form_spec({"matter": PILOT_MATTER, "variant": PILOT_VARIANT})
    assert res["ok"] is False
    assert "something nobody predicted" in res["problems"][0]


def test_empty_strings_from_the_form_count_as_unanswered(api):
    res = api.review({
        "matter": PILOT_MATTER, "variant": PILOT_VARIANT,
        "values": {**fixtures.BASE, "child1_name": ""},
    })
    assert "Child — Current legal name" in res["missing"]
    assert not any("child1_name" in p for p in res["problems"])
