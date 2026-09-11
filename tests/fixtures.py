"""A fixed intake used by the rendering tests.

Written out by hand rather than generated, so that a change to config/fields.json
shows up as a failing test instead of quietly changing what the golden file
compares against.

Every value is obviously fake. No real name, date of birth, county or case number
belongs in this repository — see the confidentiality note in README.md.
"""

from __future__ import annotations

from typing import Any

#: ICWA on, name change on, both parents relinquished, child under 12.
BASE: dict[str, Any] = {
    "county": "Wagoner",
    "petitioner1_name": "SAMPLE PETITIONER",
    "petitioner1_address": "100 Sample Street, Sampletown, OK 74000",
    "kinship": False,
    "petitioner1_race": "Caucasian",
    "petitioner1_gender": "female",
    "petitioner1_dob": "1985-06-15",
    "petitioner1_birth_state": "Oklahoma",
    "child1_name": "SAMPLE CHILD",
    "child1_dob": "2015-03-02",
    "child1_race": "Caucasian",
    "child1_gender": "male",
    "child1_birth_place": "Sample Regional Hospital",
    "child1_birth_city": "Sampletown",
    "child1_birth_county": "Sample",
    "child1_birth_state": "Oklahoma",
    "child1_residence_county": "Sample",
    "name_change": True,
    "child1_new_name": "SAMPLE NEW CHILD NAME",
    "bio_mother_name": "SAMPLE BIRTH MOTHER",
    "bio_mother_status": "relinquished",
    "bio_father_name": "SAMPLE BIRTH FATHER",
    "bio_father_status": "relinquished",
    "deprived_action_county": "Sample",
    "foster_placement_date": "2024-09-21",
    "kinship": False,
    # The affidavit's figures. Obviously fake, and deliberately not round, so a
    # total that stopped being computed from them would be visible in a golden file.
    "attorney_hourly_rate": 300,
    "attorney_hours": 13,
    "filing_fee": 184.14,
    "amended_certificate_fee": 40,
    "icwa_applies": True,
    "tribe": "Sample Nation",
}

#: The step-parent matter: petitioner 1 is the biological parent, petitioner 2 is
#: the adopting spouse, and the "other" parent is the one whose consent is at issue.
STEPPARENT: dict[str, Any] = {
    "county": "Wagoner",
    "petitioner1_name": "SAMPLE PARENT",
    "petitioner1_address": "100 Sample Street, Sampletown, OK 74000",
    "kinship": False,
    "petitioner1_race": "Caucasian",
    "petitioner1_gender": "female",
    "petitioner1_dob": "1985-06-15",
    "petitioner1_birth_state": "Oklahoma",
    "petitioner2_name": "SAMPLE STEP PARENT",
    "petitioner2_race": "Caucasian",
    "petitioner2_gender": "male",
    "petitioner2_dob": "1983-02-11",
    "petitioner2_birth_state": "Oklahoma",
    "marriage_date": "2014-10-11",
    "marriage_place": "Sampletown, Oklahoma",
    "child1_name": "SAMPLE CHILD",
    "child1_dob": "2015-03-02",
    "child1_race": "Caucasian",
    "child1_gender": "male",
    "child1_birth_place": "Sample Regional Hospital",
    "child1_birth_city": "Sampletown",
    "child1_birth_county": "Sample",
    "child1_birth_state": "Oklahoma",
    "child1_residence_county": "Sample",
    "name_change": False,
    "other_parent_name": "SAMPLE OTHER PARENT",
    "other_parent_relationship": "father",
    "other_parent_consent_basis": "consents",
    "icwa_applies": False,
}


def stepparent(**overrides: Any) -> dict[str, Any]:
    merged = {**STEPPARENT, **overrides}
    return {k: v for k, v in merged.items() if v is not None}

#: The other side of every branch in the pilot template.
ALTERNATE: dict[str, Any] = {
    **BASE,
    "name_change": False,
    "bio_mother_status": "terminated",
    "bio_father_status": "terminated",
    "icwa_applies": False,
    "child1_dob": "2010-01-01",  # 16 on the fixed test date, so consent is required
}
ALTERNATE.pop("child1_new_name")
ALTERNATE.pop("tribe")


def values(**overrides: Any) -> dict[str, Any]:
    """BASE with specific answers changed, dropping any set to None."""
    merged = {**BASE, **overrides}
    return {k: v for k, v in merged.items() if v is not None}
