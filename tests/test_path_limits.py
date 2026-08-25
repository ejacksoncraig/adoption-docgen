"""Windows will not open a path of 260 characters or more.

The nasty part is where it fails. Python writes the file happily; it is *Word*
that refuses, with "Cannot open the file because the file path is more than 259
characters". So the app reports three documents written, the folder shows three
documents, and one of them cannot be opened — discovered whenever somebody next
needs it.

Names are therefore shortened to fit, and the shortening is reported rather than
done quietly.
"""

from __future__ import annotations

import sys

import pytest

from app import engine
from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="only Windows has this limit")

SHORT = Path(r"C:\AdoptionFilingGenerator\output\dhs_SAMPLE_CHILD_2026-08-25")
#: As long as the real failure: the packaged app inside a synced folder, six
#: directories down, writing a draft — 210 characters before the file name.
DEEP = Path(
    r"C:\Users\someone\OneDrive - A Considerably Longer University Name\Desktop\Adoption program starting material"
    r"\files\adoption-docgen-starter\adoption-docgen\dist\AdoptionFilingGenerator"
    r"\output\DRAFT_dhs_SAMPLE_CHILD_2026-08-25"
)

NAME = "2 Consent, Affidavit, Order - SAMPLE CHILD.docx"


def test_a_name_that_fits_is_left_alone():
    fitted, note = engine.fit_within_path_limit(SHORT, NAME)
    assert fitted == NAME
    assert note is None


def test_a_name_that_would_not_open_is_shortened():
    fitted, note = engine.fit_within_path_limit(DEEP, NAME)
    assert fitted != NAME
    assert note is not None


def test_the_shortened_path_is_actually_openable():
    fitted, _ = engine.fit_within_path_limit(DEEP, NAME)
    assert len(str(DEEP / fitted)) <= engine.PATH_LIMIT


def test_room_is_left_for_a_second_copy():
    """Generating twice into one folder appends " (2)". Fitting exactly to the
    limit would put the second copy back over it."""
    fitted, _ = engine.fit_within_path_limit(DEEP, NAME)
    second = f"{Path(fitted).stem} (2){Path(fitted).suffix}"
    assert len(str(DEEP / second)) <= engine.PATH_LIMIT


def test_shortening_keeps_the_extension():
    fitted, _ = engine.fit_within_path_limit(DEEP, NAME)
    assert fitted.endswith(".docx"), "a .docx that is not called .docx will not open in Word"


def test_shortening_does_not_leave_dangling_punctuation():
    fitted, _ = engine.fit_within_path_limit(DEEP, "1 Petition and Decree - SAMPLE CHILD.docx")
    assert not Path(fitted).stem.endswith(("-", ",", " "))


def test_the_shortening_is_reported_not_silent():
    _fitted, note = engine.fit_within_path_limit(DEEP, NAME)
    assert "shortened" in note
    assert str(engine.PATH_LIMIT) in note


def test_a_draft_prefix_survives_shortening():
    """Losing the DRAFT marker to make room would be the worst possible trade."""
    fitted, _ = engine.fit_within_path_limit(DEEP, engine.DRAFT_PREFIX + NAME)
    assert fitted.startswith(engine.DRAFT_PREFIX)


def test_a_folder_too_deep_for_any_name_refuses_with_advice():
    """When nothing can be made to fit, say so plainly rather than writing files
    nobody can open."""
    hopeless = Path("C:/" + "x" * 250)
    with pytest.raises(engine.RenderError) as exc:
        engine.fit_within_path_limit(hopeless, NAME)
    message = " ".join(exc.value.problems)
    assert "too deep" in message
    assert "Move the application" in message


# --------------------------------------------------------------------------
# through a real generation
# --------------------------------------------------------------------------


def test_every_generated_document_opens(registry, tmp_path, today):
    """The end that matters: whatever the app writes, Word can open."""
    from tests import fixtures

    result = engine.generate(registry, "dhs", "dhs_1p_1c", fixtures.BASE,
                             today=today, output_root=tmp_path)
    for generated in result.files:
        assert len(str(generated.path)) <= engine.PATH_LIMIT, generated.path.name


def test_shortening_during_generation_is_surfaced_as_a_warning(registry, tmp_path, today, monkeypatch):
    """Staff must be told why a file is not called what the configuration says."""
    from tests import fixtures

    # Pretend there is almost no room left, without needing a 200-character path.
    monkeypatch.setattr(engine, "PATH_LIMIT", len(str(tmp_path)) + 60)
    result = engine.generate(registry, "dhs", "dhs_1p_1c", fixtures.BASE,
                             today=today, output_root=tmp_path)

    assert result.warnings, "a shortened name must be reported"
    assert any("shortened" in warning for warning in result.warnings)
    for generated in result.files:
        assert generated.path.exists()


# --------------------------------------------------------------------------
# the names themselves
# --------------------------------------------------------------------------


def test_the_configured_names_fit_a_sensible_install(registry):
    """They should not need shortening anywhere reasonable — the guard is for
    pathological locations, not for everyday use."""
    folder = SHORT
    for matter in registry.matters:
        for variant in matter.variants:
            if not variant.is_ready:
                continue
            for document in variant.all_documents():
                name = engine.DRAFT_PREFIX + document.output_name.replace(
                    "{{ child1_name }}", "SAMPLE CHILD ONE"
                )
                fitted, note = engine.fit_within_path_limit(folder, name)
                assert note is None, f"{document.output_name} needed shortening: {fitted}"


def test_the_documents_of_a_filing_sort_into_filing_order(registry):
    """Numbered so Explorer lists the petition before the packet."""
    for matter in registry.matters:
        for variant in matter.variants:
            if not variant.is_ready:
                continue
            names = [d.output_name for d in variant.all_documents()]
            assert names == sorted(names), f"{variant.id}: {names}"
            assert all(name[0].isdigit() for name in names), variant.id
