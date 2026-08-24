"""Importing a form the client already filled in.

The danger here is not a crash, it is a quiet success: a column read into the
wrong field produces a complete, plausible, wrong petition. So most of these
tests are about what the importer refuses to decide.
"""

from __future__ import annotations

import csv
import json

import pytest

from app import engine, responses
from conftest import PILOT_MATTER, PILOT_VARIANT
from tests import fixtures

# A Google Forms export: a Timestamp column, question text as headings,
# "Yes"/"No" for the checkboxes, and M/D/YYYY dates.
HEADERS = [
    "Timestamp", "Email Address",
    "Your full legal name", "Your current address", "Your race", "Your gender",
    "Your date of birth", "In what state were you born?",
    "Child's current legal name", "Child's date of birth", "Child's race", "Child's gender",
    "Hospital or place of birth", "City of birth", "County of birth",
    "County the child has lived in for the past 5 years",
    "Do you want to change the child's name?", "If yes, what new name?",
    "Biological mother's full name", "Mother's rights",
    "Biological father's full name", "Father's rights",
    "County of the deprived action", "Date of foster placement",
]
ANSWERS = [
    "9/14/2026 10:04:11", "family@example.com",
    "SAMPLE PETITIONER", "100 Sample St, Sampletown OK", "Caucasian", "Female",
    "6/15/1985", "Oklahoma",
    "SAMPLE CHILD", "3/2/2015", "Caucasian", "Male",
    "Sample Regional Hospital", "Sampletown", "Sample",
    "Sample",
    "Yes", "SAMPLE NEW NAME",
    "SAMPLE BIRTH MOTHER", "Relinquished",
    "SAMPLE BIRTH FATHER", "Terminated",
    "Sample", "9/21/2024",
]


def write_csv(path, headers=HEADERS, rows=(ANSWERS,), delimiter=",", encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


@pytest.fixture
def sheet(tmp_path):
    return responses.read_sheet(write_csv(tmp_path / "responses.csv"))


@pytest.fixture
def columns(sheet, registry, pilot_groups):
    return responses.match_columns(sheet, registry.schema, pilot_groups)


# --------------------------------------------------------------------------
# reading the file
# --------------------------------------------------------------------------


def test_reads_a_google_export(sheet):
    assert sheet.headers[0] == "Timestamp"      # the byte-order mark is not part of it
    assert len(sheet.rows) == 1


def test_reads_tab_separated_too(tmp_path):
    sheet = responses.read_sheet(write_csv(tmp_path / "r.tsv", delimiter="\t"))
    assert sheet.headers[2] == "Your full legal name"


@pytest.mark.parametrize(
    "content, expected",
    [("", "is empty"),
     ("a,b,c\n", "no responses under them")],
)
def test_unusable_files_say_why(tmp_path, content, expected):
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        responses.read_sheet(path)


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        responses.read_sheet(tmp_path / "nope.csv")


def test_a_binary_file_is_refused_with_advice(tmp_path):
    path = tmp_path / "responses.xlsx"
    path.write_bytes(b"PK\x03\x04" + bytes(range(256)) * 4)
    with pytest.raises(ValueError, match="CSV"):
        responses.read_sheet(path)


# --------------------------------------------------------------------------
# matching columns — what it must never get wrong
# --------------------------------------------------------------------------


def find(columns, header):
    return next(column for column in columns if column.header == header)


def test_the_parents_date_of_birth_does_not_become_the_childs(columns):
    """Six fields are labelled "Date of birth". Putting a parent's in a child's
    petition is the single most damaging thing this importer could do."""
    assert find(columns, "Your date of birth").field_id == "petitioner1_dob"
    assert find(columns, "Child's date of birth").field_id == "child1_dob"


def test_the_group_named_in_the_heading_decides(columns):
    assert find(columns, "Your full legal name").field_id == "petitioner1_name"
    assert find(columns, "Child's current legal name").field_id == "child1_name"
    assert find(columns, "Your race").field_id == "petitioner1_race"
    assert find(columns, "Child's race").field_id == "child1_race"


def test_mother_and_father_are_not_interchanged(columns):
    assert find(columns, "Biological mother's full name").field_id == "bio_mother_name"
    assert find(columns, "Biological father's full name").field_id == "bio_father_name"
    assert find(columns, "Mother's rights").field_id == "bio_mother_status"
    assert find(columns, "Father's rights").field_id == "bio_father_status"


def test_a_heading_that_could_mean_two_fields_is_left_for_a_person(registry, pilot_groups, tmp_path):
    """"State of birth" is a field on the petitioner *and* on the child, and the
    heading says nothing about which. Guessing is worse than asking."""
    sheet = responses.read_sheet(write_csv(tmp_path / "r.csv", ["State of birth"], [["Oklahoma"]]))
    column = responses.match_columns(sheet, registry.schema, pilot_groups)[0]
    assert column.field_id is None
    assert column.confidence == "ambiguous"


def test_boilerplate_columns_are_ignored(columns):
    assert find(columns, "Timestamp").is_boilerplate
    assert find(columns, "Email Address").is_boilerplate
    assert find(columns, "Timestamp").field_id is None


def test_no_field_is_filled_from_two_columns(columns):
    used = [column.field_id for column in columns if column.field_id]
    assert len(used) == len(set(used))


def test_an_unrecognised_heading_is_left_unmatched(registry, pilot_groups, tmp_path):
    sheet = responses.read_sheet(
        write_csv(tmp_path / "r.csv", ["What is your favourite colour?"], [["green"]])
    )
    column = responses.match_columns(sheet, registry.schema, pilot_groups)[0]
    assert column.field_id is None


def test_a_remembered_mapping_overrides_the_guess(registry, pilot_groups, sheet):
    remembered = {"In what state were you born?": "petitioner1_birth_state"}
    columns = responses.match_columns(sheet, registry.schema, pilot_groups, remembered)
    column = find(columns, "In what state were you born?")
    assert column.field_id == "petitioner1_birth_state"
    assert column.confidence == "remembered"


def test_a_column_can_be_remembered_as_ignored(registry, pilot_groups, sheet):
    columns = responses.match_columns(
        sheet, registry.schema, pilot_groups, {"Your race": None}
    )
    assert find(columns, "Your race").field_id is None


# --------------------------------------------------------------------------
# reading the values
# --------------------------------------------------------------------------


def test_a_response_becomes_intake_answers(sheet, columns, registry):
    values, _notes = responses.to_values(sheet, 0, columns, registry.schema)

    assert values["petitioner1_name"] == "SAMPLE PETITIONER"
    assert values["child1_name"] == "SAMPLE CHILD"
    assert values["petitioner1_dob"] == "1985-06-15"      # 6/15/1985, converted
    assert values["child1_dob"] == "2015-03-02"
    assert values["name_change"] is True                  # "Yes"
    assert values["petitioner1_gender"] == "female"       # "Female" -> the option
    assert values["bio_mother_status"] == "relinquished"


@pytest.mark.parametrize(
    "written, expected",
    [("3/2/2015", "2015-03-02"), ("2015-03-02", "2015-03-02"), ("03/02/2015", "2015-03-02"),
     ("March 2, 2015", "2015-03-02"), ("3/2/2015 14:03:01", "2015-03-02")],
)
def test_dates_are_understood_however_the_form_wrote_them(registry, written, expected):
    field = registry.schema.fields["child1_dob"]
    assert responses.coerce(written, field) == (expected, None)


@pytest.mark.parametrize("written, expected", [("Yes", True), ("yes", True), ("TRUE", True),
                                               ("No", False), ("n", False), ("", None)])
def test_yes_and_no_become_real_booleans(registry, written, expected):
    field = registry.schema.fields["name_change"]
    value, problem = responses.coerce(written, field)
    assert value is expected
    assert problem is None


def test_an_answer_that_is_not_yes_or_no_is_not_guessed(registry):
    value, problem = responses.coerce("maybe, we're not sure", registry.schema.fields["name_change"])
    assert value is None
    assert "yes or no" in problem


def test_an_option_that_is_not_on_the_list_is_not_guessed(registry):
    value, problem = responses.coerce("gave up her rights", registry.schema.fields["bio_mother_status"])
    assert value is None
    assert "is not one of" in problem


def test_a_date_that_is_not_a_date_is_reported_not_stored(registry):
    value, problem = responses.coerce("sometime in the spring", registry.schema.fields["child1_dob"])
    assert value is None
    assert "is not a date" in problem


def test_a_bad_value_leaves_the_field_blank_and_says_so(registry, pilot_groups, tmp_path):
    bad = list(ANSWERS)
    bad[HEADERS.index("Child's date of birth")] = "not a date"
    sheet = responses.read_sheet(write_csv(tmp_path / "r.csv", HEADERS, [bad]))
    columns = responses.match_columns(sheet, registry.schema, pilot_groups)

    values, notes = responses.to_values(sheet, 0, columns, registry.schema)
    assert "child1_dob" not in values
    assert any("is not a date" in note for note in notes)


def test_an_unmapped_column_with_an_answer_is_reported(sheet, columns, registry):
    _values, notes = responses.to_values(sheet, 0, columns, registry.schema)
    assert any("In what state were you born?" in note for note in notes)


def test_asking_for_a_response_that_is_not_there(sheet, columns, registry):
    with pytest.raises(ValueError, match="no response number 4"):
        responses.to_values(sheet, 3, columns, registry.schema)


def test_several_responses_can_be_told_apart(registry, pilot_groups, tmp_path):
    second = list(ANSWERS)
    second[HEADERS.index("Child's current legal name")] = "SAMPLE SECOND CHILD"
    sheet = responses.read_sheet(write_csv(tmp_path / "r.csv", HEADERS, [ANSWERS, second]))
    assert len(sheet.rows) == 2
    assert "SAMPLE SECOND CHILD" in sheet.row_labels()[1]

    columns = responses.match_columns(sheet, registry.schema, pilot_groups)
    values, _ = responses.to_values(sheet, 1, columns, registry.schema)
    assert values["child1_name"] == "SAMPLE SECOND CHILD"


# --------------------------------------------------------------------------
# from an export to a filed document
# --------------------------------------------------------------------------


def test_an_imported_response_generates_documents(registry, sheet, columns, tmp_path, today):
    """The whole point: what the client typed once ends up in the petition."""
    values, _notes = responses.to_values(sheet, 0, columns, registry.schema)

    # what the office adds afterwards
    values.update({
        "county": "Wagoner",
        "petitioner1_birth_state": "Oklahoma",
        "child1_birth_state": "Oklahoma",
        "icwa_applies": False,
        "attorney_fees_summary": fixtures.BASE["attorney_fees_summary"],
    })
    assert registry.schema.missing_required(values, registry.variant(PILOT_MATTER, PILOT_VARIANT).field_groups) == []

    result = engine.generate(registry, PILOT_MATTER, PILOT_VARIANT, values,
                             today=today, output_root=tmp_path)
    text = engine.document_text(result.files[0].path)
    assert "SAMPLE CHILD" in text
    assert "born on the 2nd day of March, 2015" in text     # the child's date, not the parent's
    assert "is now 41 years of age" in text                 # the parent's, worked out from theirs
    assert "SAMPLE NEW NAME" in text


# --------------------------------------------------------------------------
# remembering the mapping
# --------------------------------------------------------------------------


def test_a_confirmed_mapping_is_remembered(columns, tmp_path):
    responses.save_mapping(columns, config_dir=tmp_path)
    remembered = responses.load_mapping(config_dir=tmp_path)
    assert remembered["Your date of birth"] == "petitioner1_dob"
    assert remembered["Child's date of birth"] == "child1_dob"


def test_the_remembered_mapping_holds_no_client_information(columns, tmp_path):
    """It lives in config/, which is in the repository. Headings only, never answers."""
    path = responses.save_mapping(columns, config_dir=tmp_path)
    written = path.read_text(encoding="utf-8")
    for answer in ("SAMPLE PETITIONER", "SAMPLE CHILD", "3/2/2015", "family@example.com"):
        assert answer not in written


def test_remembering_adds_to_what_is_already_there(columns, tmp_path):
    responses.save_mapping(columns, config_dir=tmp_path)
    other = [responses.Column(index=0, header="A different form's question", field_id="child1_race")]
    responses.save_mapping(other, config_dir=tmp_path)

    remembered = responses.load_mapping(config_dir=tmp_path)
    assert remembered["A different form's question"] == "child1_race"
    assert remembered["Your date of birth"] == "petitioner1_dob"


def test_a_corrupt_mapping_file_does_not_stop_an_import(tmp_path):
    (tmp_path / responses.MAPPING_FILE).write_text("{ not json", encoding="utf-8")
    assert responses.load_mapping(config_dir=tmp_path) == {}


def test_the_mapping_file_is_readable_json(columns, tmp_path):
    path = responses.save_mapping(columns, config_dir=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "_comment" in written
    assert written["columns"]["Your race"] == "petitioner1_race"
