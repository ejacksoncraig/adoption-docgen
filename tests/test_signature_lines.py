"""The rules people sign on, measured against the room they have.

A rule one character too long does not look like a mistake in the template. It
looks like the document has a stray line in it, because Word wraps the last
underscore onto a line of its own and, if the paragraph is justified, stretches
the one above to meet it. It reached the office twice before it was understood.

The arithmetic is exact rather than approximate: the underscore in Times New
Roman is half an em, so at 12pt each one costs 120 twips, and the space a
paragraph has is the text width less its left indent.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

#: Twips consumed by one underscore of Times New Roman at 12pt.
UNDERSCORE = 120

TEMPLATES = sorted(Path("templates").rglob("*.docx"))

PARAGRAPH = re.compile(r'<w:p\b(?:[^>]*/>|.*?</w:p>)', re.S)
RUN_TEXT = re.compile(r'<w:t(?:\s[^>]*)?>(.*?)</w:t>', re.S)


def text_width(xml: str) -> int:
    page = re.search(r'<w:pgSz w:w="(\d+)"[^/]*/><w:pgMar([^/]*)/>', xml)
    margins = dict(re.findall(r'w:(left|right)="(\d+)"', page.group(2)))
    return int(page.group(1)) - int(margins["left"]) - int(margins["right"])


def rules(path: Path):
    """Every paragraph that is nothing but a rule, with the room it has."""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    room = text_width(xml)
    for paragraph in PARAGRAPH.findall(xml):
        line = "".join(RUN_TEXT.findall(paragraph)).strip()
        if not line or set(line) != {"_"}:
            continue
        indent = re.search(r'<w:ind [^>]*w:left="(\d+)"', paragraph)
        yield line, room - (int(indent.group(1)) if indent else 0)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_no_signature_line_wraps_onto_a_second_line(template):
    for line, room in rules(template):
        assert len(line) * UNDERSCORE <= room, (
            f"{len(line)} underscores need {len(line) * UNDERSCORE} twips "
            f"and the paragraph has {room}"
        )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_a_signature_line_is_long_enough_to_sign_on(template):
    """The other direction. Two inches is not generous; it is a minimum."""
    for line, _room in rules(template):
        assert len(line) * UNDERSCORE >= 2880, f"{len(line)} underscores is a dash"
