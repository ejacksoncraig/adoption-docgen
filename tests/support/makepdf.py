"""Build a small text PDF, so the PDF reader can be tested on a real file.

Not a general PDF writer — just enough of one to lay out lines of text the way a
printed form does, producing a file pypdf genuinely has to parse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_pdf(path: Path, lines: Sequence[str], leading: int = 18) -> Path:
    """Write `lines` down the page, breaking onto a new page when it runs out."""
    per_page = 44
    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[]]

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)          # object numbers are 1-based

    # WinAnsiEncoding matters: without it a reader decodes character 39 by Adobe
    # StandardEncoding, where it is a closing quote, and every apostrophe comes
    # back as U+2019. Real PDF producers declare it, so the fixture must too, or
    # it is not testing what the app will actually be handed.
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    for page_lines in pages:
        stream = ["BT", "/F1 11 Tf", f"{leading} TL", "60 760 Td"]
        for line in page_lines:
            stream.append(f"({_escape(line)}) Tj T*")
        stream.append("ET")
        data = "\n".join(stream).encode("latin-1", "replace")
        content_ids.append(add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(data), data)))

    pages_id = len(objects) + len(pages) + 1
    for content_id in content_ids:
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, font, content_id)
        ))

    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    start = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, start
    )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
