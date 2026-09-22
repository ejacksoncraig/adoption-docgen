"""Turn packaging/icon.png into the two formats PyInstaller wants.

    python packaging/make_icons.py

PyInstaller will not take a .png: macOS needs .icns and Windows needs .ico, and
each is a container holding the same picture at a dozen sizes, because a Dock
tile and a 16px taskbar slot are not the same problem. Both outputs are
committed, so a Windows build never has to produce an .icns and this script
never has to run anywhere but a Mac — sips and iconutil ship with macOS and
exist nowhere else.

Rerun it after replacing icon.png, and commit what it writes.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "icon.png"

#: The sizes iconutil expects, by the names it insists on. Each @2x is the same
#: pixel count as the next size up; macOS picks between them by display scale.
ICONSET = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]

#: Windows reads whichever entry is closest to the slot it is filling.
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def resize(source: Path, size: int, destination: Path) -> Path:
    subprocess.run(["sips", "-z", str(size), str(size), str(source),
                    "--out", str(destination)],
                   check=True, capture_output=True)
    return destination


def make_icns(out: Path) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        iconset = Path(scratch) / "icon.iconset"
        iconset.mkdir()
        for size, name in ICONSET:
            resize(SOURCE, size, iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                       check=True)


def make_ico(out: Path) -> None:
    """ICO is a directory of images. Since Vista each may be a whole PNG, which
    is what everything writes now and what keeps the 256px entry a sane size."""
    with tempfile.TemporaryDirectory() as scratch:
        images = [resize(SOURCE, size, Path(scratch) / f"{size}.png").read_bytes()
                  for size in ICO_SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))       # reserved, type=icon, count
    offset = len(header) + 16 * len(images)
    directory, payload = b"", b""
    for size, image in zip(ICO_SIZES, images):
        directory += struct.pack(
            "<BBBBHHII",
            size % 256, size % 256,                       # 256 is written as 0
            0, 0,                                         # palette, reserved
            1, 32,                                        # colour planes, bit depth
            len(image), offset,
        )
        payload += image
        offset += len(image)
    out.write_bytes(header + directory + payload)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This needs sips and iconutil, which are macOS only. "
                 "The .icns and .ico it writes are committed; you should not "
                 "need to run it.")
    if not SOURCE.exists():
        sys.exit(f"{SOURCE} is missing")
    make_icns(HERE / "icon.icns")
    make_ico(HERE / "icon.ico")
    for name in ("icon.icns", "icon.ico"):
        print(f"  {name}  {(HERE / name).stat().st_size:,} bytes")
