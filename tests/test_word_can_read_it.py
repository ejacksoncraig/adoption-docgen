"""Element order, which Word enforces and complains about uselessly.

A .docx whose <w:pPr> or <w:rPr> lists its children in the wrong order makes
Word refuse the file: "Word found unreadable content in ... Do you want to
recover the contents of this document?" It does not name the element, the
paragraph, or the document part. Click Yes and the recovered copy looks
perfectly correct, which is how a filing can go out wrong for weeks without
anybody being able to say what is wrong with it.

The schema declares these as sequences, so the order is not a matter of taste.
These lists are the sequences from ECMA-376 for the property elements our
templates actually use. Only direct children count — <w:rPr> nests inside
<w:pPr>, and its own spacing has nothing to do with the paragraph's.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from app import engine
from tests import fixtures

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SCHEMA = {
    "pPr": "pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr "
           "suppressLineNumbers pBdr shd tabs suppressAutoHyphens kinsoku wordWrap "
           "overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd "
           "snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc "
           "textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr "
           "sectPr pPrChange".split(),
    "rPr": "ins del moveFrom moveTo rStyle rFonts b bCs i iCs caps smallCaps strike "
           "dstrike outline shadow emboss imprint noProof snapToGrid vanish webHidden "
           "color spacing w kern position sz szCs highlight u effect bdr shd fitText "
           "vertAlign rtl cs em lang eastAsianLayout specVanish oMath rPrChange".split(),
    "tblPr": "tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize "
             "tblStyleColBandSize tblW jc tblCellSpacing tblInd tblBorders shd tblLayout "
             "tblCellMar tblLook tblCaption tblDescription tblPrChange".split(),
    "tcPr": "cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar "
            "textDirection tcFitText vAlign hideMark".split(),
    "sectPr": "headerReference footerReference footnotePr endnotePr type pgSz pgMar "
              "paperSrc pgBorders lnNumType pgNumType cols formProt vAlign noEndnote "
              "titlePg textDirection bidi rtlGutter docGrid printerSettings "
              "sectPrChange".split(),
}

TEMPLATES = sorted(Path("templates").rglob("*.docx"))


def out_of_order(path: Path) -> list[str]:
    wrong = []
    archive = zipfile.ZipFile(path)
    for part in archive.namelist():
        if not (part.startswith("word/") and part.endswith(".xml")):
            continue
        root = etree.fromstring(archive.read(part))
        for parent, sequence in SCHEMA.items():
            for element in root.iter(f"{W}{parent}"):
                names = [etree.QName(child).localname for child in element
                         if isinstance(child.tag, str)]
                ranks = [sequence.index(n) for n in names if n in sequence]
                if ranks != sorted(ranks):
                    wrong.append(f"{part} <w:{parent}>: {' '.join(names)}")
    return wrong


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_a_template_lists_its_properties_in_schema_order(template):
    assert out_of_order(template) == []


def test_what_we_hand_the_office_lists_its_properties_in_schema_order(
    registry, tmp_path, today
):
    """The templates being right is not quite the point: the office opens the
    generated documents, and rendering inserts a signature image into some of
    them. This is the file Word actually sees."""
    values = fixtures.values(concurrent_jurisdiction=True, juvenile_records=True,
                             filing_cover_sheets=True)
    result = engine.generate(registry, "dhs", "dhs_1p_1c", values,
                             today=today, output_root=tmp_path)
    assert result.files
    for generated in result.files:
        assert out_of_order(generated.path) == [], generated.path.name
