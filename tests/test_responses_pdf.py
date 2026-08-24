"""Reading a client's form when it arrives as a PDF.

A PDF is a picture of a form: no columns, just text in an order — or, if someone
scanned a paper copy, no text at all. These tests cover finding the questions in
it, keeping each answer attached to the right one, and refusing clearly when
there is nothing to read.
"""

from __future__ import annotations

import pytest

from app import responses
from tests.support.makepdf import write_pdf

QA = [
    ("Your full legal name", "SAMPLE PETITIONER"),
    ("Your current address", "100 Sample St, Sampletown OK 74000"),
    ("Your race", "Caucasian"),
    ("Your gender", "Female"),
    ("Your date of birth", "6/15/1985"),
    ("Child's current legal name", "SAMPLE CHILD"),
    ("Child's date of birth", "3/2/2015"),
    ("Child's race", "Caucasian"),
    ("Child's gender", "Male"),
    ("Hospital or place of birth", "Sample Regional Hospital"),
    ("City of birth", "Sampletown"),
    ("County of birth", "Sample"),
    ("County the child has lived in for the past 5 years", "Sample"),
    ("Do you want to change the child's name?", "Yes"),
    ("If yes, what new name?", "SAMPLE NEW NAME"),
    ("Biological mother's full name", "SAMPLE BIRTH MOTHER"),
    ("Mother's rights", "Relinquished"),
    ("Biological father's full name", "SAMPLE BIRTH FATHER"),
    ("Father's rights", "Terminated"),
    ("County of the deprived action", "Sample"),
    ("Date of foster placement", "9/21/2024"),
]

#: What a Google Form response carries once it has been printed to PDF.
FOOTER = [
    "https://docs.google.com/forms/d/e/1FAIpQL/viewform",
    "1/2",
    "This content is neither created nor endorsed by Google.",
]


def printed(tmp_path, pairs=QA, title="Adoption Intake Questionnaire"):
    lines = [title, ""]
    for question, answer in pairs:
        lines += [question, answer, ""]
    return write_pdf(tmp_path / "response.pdf", lines + FOOTER)


@pytest.fixture
def sheet(tmp_path, registry, pilot_groups):
    return responses.read_pdf(printed(tmp_path), registry.schema, pilot_groups)


@pytest.fixture
def found(sheet):
    return dict(zip(sheet.headers, sheet.rows[0]))


# --------------------------------------------------------------------------
# finding the questions
# --------------------------------------------------------------------------


def test_every_question_and_answer_is_found(sheet):
    assert len(sheet.headers) == len(QA)
    assert all(sheet.rows[0]), "a question was left without its answer"


def test_each_answer_stays_with_its_own_question(found):
    assert found["Your full legal name"] == "SAMPLE PETITIONER"
    assert found["Child's current legal name"] == "SAMPLE CHILD"
    assert found["Your date of birth"] == "6/15/1985"
    assert found["Child's date of birth"] == "3/2/2015"


def test_an_answer_is_not_swallowed_by_the_question_below_it(found):
    # "Male" followed by "Hospital or place of birth" once read as one long
    # question, and the gender answer vanished with it.
    assert found["Child's gender"] == "Male"
    assert found["Hospital or place of birth"] == "Sample Regional Hospital"


def test_an_answer_that_reads_like_a_question_is_still_an_answer(sheet):
    # "SAMPLE PETITIONER" carries a group word and was once taken for a heading.
    assert "SAMPLE PETITIONER" not in sheet.headers


def test_printed_footers_are_not_read_as_answers(sheet):
    joined = " ".join(sheet.rows[0])
    assert "docs.google.com" not in joined
    assert "neither created nor endorsed" not in joined


def test_a_question_wrapped_over_two_lines_is_still_found(tmp_path, registry, pilot_groups):
    lines = ["County the child has lived in for the", "past 5 years", "", "Sample", ""]
    path = write_pdf(tmp_path / "wrapped.pdf", lines)
    sheet = responses.read_pdf(path, registry.schema, pilot_groups)
    assert sheet.rows[0][0] == "Sample"


def test_a_question_the_client_skipped_does_not_steal_the_next_answer(tmp_path, registry, pilot_groups):
    lines = ["Child's current legal name", "Child's race", "Caucasian"]
    path = write_pdf(tmp_path / "skipped.pdf", lines)
    sheet = responses.read_pdf(path, registry.schema, pilot_groups)

    found = dict(zip(sheet.headers, sheet.rows[0]))
    assert found.get("Child's race") == "Caucasian"
    assert found.get("Child's current legal name", "") != "Caucasian"


# --------------------------------------------------------------------------
# through to intake answers
# --------------------------------------------------------------------------


def test_a_printed_form_becomes_intake_answers(sheet, registry, pilot_groups):
    columns = responses.match_columns(sheet, registry.schema, pilot_groups)
    values, _notes = responses.to_values(sheet, 0, columns, registry.schema)

    assert values["petitioner1_name"] == "SAMPLE PETITIONER"
    assert values["child1_name"] == "SAMPLE CHILD"
    assert values["petitioner1_dob"] == "1985-06-15"
    assert values["child1_dob"] == "2015-03-02"
    assert values["name_change"] is True
    assert values["child1_new_name"] == "SAMPLE NEW NAME"
    assert values["bio_mother_status"] == "relinquished"
    assert values["bio_father_status"] == "terminated"


def test_the_parents_details_do_not_become_the_childs(sheet, registry, pilot_groups):
    columns = responses.match_columns(sheet, registry.schema, pilot_groups)
    values, _ = responses.to_values(sheet, 0, columns, registry.schema)
    assert values["petitioner1_dob"] != values["child1_dob"]
    assert values["petitioner1_name"] == "SAMPLE PETITIONER"
    assert values["child1_name"] == "SAMPLE CHILD"


# --------------------------------------------------------------------------
# what it refuses
# --------------------------------------------------------------------------


def test_a_scanned_pdf_says_there_is_nothing_to_read(tmp_path, registry, pilot_groups):
    # No text layer means a photograph of a form. Nothing to parse, and saying so
    # is far better than an empty intake that looks finished.
    path = write_pdf(tmp_path / "scan.pdf", [])
    with pytest.raises(ValueError, match="no text in it"):
        responses.read_pdf(path, registry.schema, pilot_groups)


def test_a_pdf_of_something_else_entirely_is_refused(tmp_path, registry, pilot_groups):
    path = write_pdf(tmp_path / "other.pdf", ["Invoice 4471", "Amount due", "$300.00", "Thank you"])
    with pytest.raises(ValueError, match="does not appear to hold answers"):
        responses.read_pdf(path, registry.schema, pilot_groups)


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path, registry, pilot_groups):
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"this is not a pdf at all")
    with pytest.raises(ValueError, match="could not be opened as a PDF"):
        responses.read_pdf(path, registry.schema, pilot_groups)


def test_a_missing_pdf_says_so(tmp_path, registry, pilot_groups):
    with pytest.raises(ValueError, match="does not exist"):
        responses.read_pdf(tmp_path / "nope.pdf", registry.schema, pilot_groups)


def test_without_a_schema_there_is_nothing_to_look_for(tmp_path):
    with pytest.raises(ValueError, match="choose the adoption type first"):
        responses.read_pdf(printed(tmp_path))


# --------------------------------------------------------------------------
# whichever way the file arrives
# --------------------------------------------------------------------------


def test_read_any_takes_a_pdf(tmp_path, registry, pilot_groups):
    sheet = responses.read_any(printed(tmp_path), registry.schema, pilot_groups)
    assert len(sheet.headers) == len(QA)


def test_read_any_still_takes_a_spreadsheet(tmp_path, registry, pilot_groups):
    path = tmp_path / "r.csv"
    path.write_text("Child's current legal name\nSAMPLE CHILD\n", encoding="utf-8")
    sheet = responses.read_any(path, registry.schema, pilot_groups)
    assert sheet.headers == ["Child's current legal name"]


def test_a_remembered_heading_is_recognised_however_it_is_worded(tmp_path, registry, pilot_groups):
    # Once staff have confirmed a heading it is a question by definition, even one
    # no amount of resemblance would have found.
    lines = ["Kiddo goes by", "SAMPLE CHILD", "", "Child's race", "Caucasian"]
    path = write_pdf(tmp_path / "odd.pdf", lines)

    sheet = responses.read_pdf(path, registry.schema, pilot_groups, {"Kiddo goes by": "child1_name"})
    assert "Kiddo goes by" in sheet.headers
    assert sheet.rows[0][sheet.headers.index("Kiddo goes by")] == "SAMPLE CHILD"
