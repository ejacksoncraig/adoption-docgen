"""The attorney's signature image: where it is kept, and how big it prints.

One image per installation, stored beside `config/settings.json` as
`config/signature.<ext>`. It belongs to the office rather than to a matter, for
the same reason the attorney's name and bar number do — it is the same on every
filing, and nobody should be attaching it per intake.

Nothing here decides *whether* to sign. That is a choice made at generation time
(see `app/engine.py::generate`), because a filing the attorney means to sign by
hand is an ordinary thing to want and the office should not have to delete the
image to get one.

Validation is done by python-docx's own image reader rather than by looking at
the file extension. If this module accepts a file, `docxtpl` can definitely embed
it; the alternative is a file that uploads happily and then fails at the moment
someone is trying to generate a filing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx.image.image import Image
from docx.shared import Mm

from app.registry import CONFIG_DIR

#: Kept as `signature.<ext>`. The extension is whatever the image actually is,
#: not what it was called when it arrived.
STEM = "signature"

#: The formats python-docx can embed. A PDF or a HEIC photograph from a phone is
#: a reasonable thing for someone to try, and a clear "no" beats a stack trace.
SUFFIXES = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}

#: The box the signature is scaled to fit, in millimetres — about 0.5" tall and
#: 2.4" wide. Height alone is not enough: a signature scanned as a long thin
#: strip would come out four inches wide and run off the end of its line.
MAX_HEIGHT_MM = 12.0
MAX_WIDTH_MM = 60.0

#: Bigger than any signature scan, small enough that a photograph of a whole
#: page — the usual mistake — is refused rather than embedded at 40 MB.
MAX_BYTES = 8 * 1024 * 1024


class SignatureError(Exception):
    """The file offered is not a signature this program can print."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def config_dir(override: Path | None = None) -> Path:
    return override or CONFIG_DIR


def stored_path(config: Path | None = None) -> Path | None:
    """The signature on file, or None. At most one ever exists."""
    folder = config_dir(config)
    for suffix in SUFFIXES.values():
        candidate = folder / f"{STEM}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _inspect(blob: bytes) -> Image:
    try:
        return Image.from_blob(blob)
    except Exception as exc:  # python-docx raises several unrelated types
        detail = str(exc).strip()
        raise SignatureError([
            "That file is not an image this program can put into a document.",
            "A PNG or JPEG scan of the signature works; a PDF, or a HEIC photo "
            "straight off a phone, does not." + (f" ({detail})" if detail else ""),
        ]) from exc


def size_for(blob: bytes) -> tuple[Any, Any]:
    """Width and height to print at, scaled to fit inside the box, aspect kept."""
    image = _inspect(blob)
    width, height = float(image.px_width), float(image.px_height)
    if width <= 0 or height <= 0:
        raise SignatureError(["That image reports no width or height."])
    scale = min(MAX_WIDTH_MM / width, MAX_HEIGHT_MM / height)
    return Mm(width * scale), Mm(height * scale)


def save(blob: bytes, config: Path | None = None) -> Path:
    """Store the image, replacing whatever was there. Returns where it landed."""
    if not blob:
        raise SignatureError(["That file is empty."])
    if len(blob) > MAX_BYTES:
        raise SignatureError([
            f"That file is {len(blob) // (1024 * 1024)} MB, which is far larger than a "
            f"signature scan — the limit is {MAX_BYTES // (1024 * 1024)} MB.",
            "It is probably a photograph of a whole page rather than a cropped signature.",
        ])

    image = _inspect(blob)
    suffix = SUFFIXES.get(image.content_type.split("/")[-1].upper())
    if suffix is None:
        suffix = SUFFIXES.get((image.ext or "").lstrip(".").upper())
    if suffix is None:
        raise SignatureError([
            f"That is a {image.content_type} image, which cannot be embedded in a .docx.",
            "Save it as a PNG — a signature on a transparent background prints best.",
        ])

    folder = config_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    remove(config)                       # never leave two signatures on disk
    target = folder / f"{STEM}{suffix}"
    target.write_bytes(blob)
    return target


def remove(config: Path | None = None) -> bool:
    """Delete the stored signature. True if there was one."""
    found = False
    folder = config_dir(config)
    for suffix in SUFFIXES.values():
        candidate = folder / f"{STEM}{suffix}"
        if candidate.exists():
            candidate.unlink()
            found = True
    return found


def describe(config: Path | None = None) -> dict[str, Any]:
    """What the office details screen needs to show about the signature."""
    path = stored_path(config)
    if path is None:
        return {"present": False}
    blob = path.read_bytes()
    try:
        image = _inspect(blob)
        pixels = f"{image.px_width} × {image.px_height}"
    except SignatureError:
        pixels = "unreadable"
    return {
        "present": True,
        "path": str(path),
        "name": path.name,
        "pixels": pixels,
        "kilobytes": max(1, len(blob) // 1024),
    }
