"""Golden-file test.

Renders the pilot template with a fixed intake and compares the extracted text,
word for word, against a committed file. Its job is to catch the edit that leaves
a document that still *renders* but no longer *reads* correctly — a conditional
whose branches got swapped, a paragraph deleted along with its number, a token
replaced by a literal.

When a template change is intentional, regenerate the expected text with

    python -m tests.test_golden --update

and read the diff in the commit. Reviewing that diff is the point of this test.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

import pytest

from app import engine
from app.registry import Registry
from conftest import PILOT_MATTER, PILOT_VARIANT, TODAY
from tests import fixtures

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN = GOLDEN_DIR / "dhs_petition_decree_1p_1c.txt"


def normalise(text: str) -> str:
    """Compare wording, not whitespace: Word's tabs and blank runs are not the point."""
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) + "\n"


def render_pilot(registry: Registry, tmp_path: Path) -> str:
    result = engine.generate(
        registry, PILOT_MATTER, PILOT_VARIANT, fixtures.BASE, today=TODAY, output_root=tmp_path
    )
    return normalise(engine.document_text(result.files[0].path))


def test_pilot_template_renders_exactly_as_recorded(registry, tmp_path):
    if not GOLDEN.exists():
        pytest.fail(f"{GOLDEN} is missing. Regenerate it with: python -m tests.test_golden --update")

    actual = render_pilot(registry, tmp_path)
    expected = GOLDEN.read_text(encoding="utf-8")
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile="expected (committed)", tofile="actual (rendered now)", lineterm="", n=2,
            )
        )
        pytest.fail(
            "The pilot template no longer renders the way it was recorded.\n"
            "If the change was intended, run: python -m tests.test_golden --update\n\n" + diff
        )


def _update() -> int:
    import tempfile

    from app import intake

    registry = Registry.load()
    for key, value in intake.sample_settings(registry.schema).items():
        registry.settings[key] = value

    with tempfile.TemporaryDirectory() as tmp:
        text = render_pilot(registry, Path(tmp))

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    existed = GOLDEN.exists()
    GOLDEN.write_text(text, encoding="utf-8")
    print(f"{'Updated' if existed else 'Wrote'} {GOLDEN} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    if "--update" not in sys.argv:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(_update())
