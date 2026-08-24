"""Package the application for the machine you run this on.

    python build.py

What ends up in dist/AdoptionFilingGenerator/:

    the application               .exe on Windows, .app bundle on macOS
    config/                       fields.json, matters.json, settings.json
    templates/                    the .docx templates
    output/  intake/              empty, created on first use

PyInstaller cannot cross-compile: a Windows build must be made on Windows and a
macOS build on a Mac. The application code itself is the same on both.

config/ and templates/ are deliberately left *outside* the executable. Adding an
adoption type or a pleading has to be a matter of dropping a .docx in and editing
two JSON files — if that required a rebuild, the expandability the whole design
is built around would be gone.

Nothing here is copied into the build: no saved intake, no generated document.

Rebuilding over an existing installation replaces the program and the templates.
It must never touch what the installation has produced or been configured with —
output/, intake/ and config/settings.json are left exactly as they are. So
PyInstaller builds into a scratch directory and only its program files are copied
across; it is never pointed at a folder that holds someone's documents.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
NAME = "AdoptionFilingGenerator"
BUNDLE = DIST / NAME

#: macOS packages a windowed app as Name.app, with the binary buried inside it.
MAC = sys.platform == "darwin"

#: PyInstaller's scratch space, deliberately outside the project.
#: This repository lives in a OneDrive folder, and OneDrive holds handles on
#: directories while it syncs them — which makes PyInstaller's --clean fail with
#: "Access is denied" on build/AdoptionFilingGenerator/localpycs. Nothing in here
#: is worth syncing anyway.
WORK = Path(tempfile.gettempdir()) / "adoption-docgen-build"

#: Where PyInstaller is allowed to write and wipe freely.
STAGING = WORK / "dist"

#: Copied next to the .exe, not into it. Replaced wholesale on every build.
ALONGSIDE = ("config", "templates")

#: Created if absent, never emptied: this is the installation's own work.
KEEP_DIRS = ("output", "intake")

#: Belongs to the installation, not the repository. A rebuild must never wipe the
#: office's own attorney details, so an existing copy is carried across.
PRESERVE = ("config/settings.json",)

#: Packages PyInstaller does not always find on its own, and the check that they
#: made it in. pypdf is imported inside a function — only when a client's form
#: arrives as a PDF — and PyInstaller's scan missed it, so the packaged app was
#: built without it and would have failed at the moment someone used the feature.
COLLECT = ("webview", "pypdf")

#: The built application checks its own dependencies; see main.self_check. Asking
#: the .exe whether it can import what it needs beats inspecting the folder, which
#: only sees packages PyInstaller happened to leave loose — the pure-Python ones
#: live inside the archive and look missing from the outside.


def run_pyinstaller() -> None:
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",                      # no console window behind the app
        "--name", NAME,
        "--add-data", f"{ROOT / 'app' / 'ui'}{separator}ui",
        *[argument for package in COLLECT for argument in ("--collect-all", package)],
        "--distpath", str(STAGING),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        str(ROOT / "app" / "main.py"),
    ]
    WORK.mkdir(parents=True, exist_ok=True)
    print(" ".join(command), "\n")
    subprocess.run(command, check=True)


def _clear_readonly(func, path, _exc_info) -> None:
    """Retry a delete after clearing the read-only bit.

    OneDrive marks synced folders read-only reparse points, which makes an
    ordinary rmtree fail with "Access is denied" on a repository kept in a
    OneDrive folder — as this one is.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove(path: Path) -> None:
    """Delete a program file or folder, explaining the usual cause of failure."""
    try:
        if path.is_dir():
            shutil.rmtree(path, onexc=_clear_readonly)
        elif path.exists():
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
    except (PermissionError, OSError) as exc:
        raise SystemExit(
            f"Cannot replace {path}: {exc}.\n"
            f"Close the application if it is running, let OneDrive finish syncing, "
            f"and run this again."
        ) from exc


def install_program() -> None:
    """Copy the freshly built program over the installation, and nothing else."""
    built = STAGING / NAME
    BUNDLE.mkdir(parents=True, exist_ok=True)

    for item in built.iterdir():
        target = BUNDLE / item.name
        _remove(target)
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        print(f"  installed {item.name}")


def built_executable() -> Path:
    """The thing to run, wherever this platform put it."""
    if MAC:
        inside = BUNDLE / f"{NAME}.app" / "Contents" / "MacOS" / NAME
        return inside if inside.exists() else BUNDLE / NAME
    return BUNDLE / f"{NAME}.exe"


def verify_bundle() -> str | None:
    """Ask the built application whether it can import everything it needs.

    Returns None when it is sound, or a description of what went wrong.
    """
    exe = built_executable()
    if not exe.exists():
        return f"the built application is not where it was expected ({exe})"
    try:
        finished = subprocess.run(
            [str(exe), "--self-check"], capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the built application could not be run ({exc})"

    if finished.returncode != 0:
        return (finished.stderr or finished.stdout or "").strip() or f"exit code {finished.returncode}"
    return None


def read_preserved() -> dict[str, str]:
    """Take a copy of the installation's own settings before anything is deleted.

    Read at the very start of the build, not just before the delete that would
    destroy it: shutil.rmtree removes files depth-first and can fail partway, so a
    build that dies while clearing config/ can leave the folder there with the
    settings already gone.
    """
    kept = {}
    for relative in PRESERVE:
        existing = BUNDLE / relative
        if existing.exists() and existing.read_text(encoding="utf-8").strip():
            kept[relative] = existing.read_text(encoding="utf-8")
    return kept


def copy_alongside(kept: dict[str, str]) -> None:
    for name in ALONGSIDE:
        target = BUNDLE / name
        _remove(target)
        shutil.copytree(
            ROOT / name, target,
            ignore=shutil.ignore_patterns("_source", "~$*", "*.doc", "__pycache__"),
        )
        print(f"  copied {name}/")

    for relative, content in kept.items():
        (BUNDLE / relative).write_text(content, encoding="utf-8")
        print(f"  kept the installation's own {relative}")

    for name in KEEP_DIRS:
        directory = BUNDLE / name
        existing = len(list(directory.rglob("*"))) if directory.exists() else 0
        directory.mkdir(parents=True, exist_ok=True)
        print(f"  left {name}/ alone ({existing} item(s))" if existing else f"  created empty {name}/")


def main() -> int:
    if not (ROOT / "config" / "matters.json").exists():
        print("Run this from the repository root.", file=sys.stderr)
        return 2

    kept = read_preserved()

    run_pyinstaller()
    if not (STAGING / NAME).exists():
        print(f"PyInstaller did not produce {STAGING / NAME}", file=sys.stderr)
        return 1

    install_program()
    copy_alongside(kept)
    shutil.rmtree(STAGING, ignore_errors=True)

    problem = verify_bundle()
    if problem:
        print("", file=sys.stderr)
        print("The build is incomplete. Do not ship this copy.", file=sys.stderr)
        print(problem, file=sys.stderr)
        print("Add the missing package to COLLECT in this script, then build again.",
              file=sys.stderr)
        return 1
    print("  self-check: all dependencies present")

    print(f"\nBuilt {BUNDLE / (NAME + '.exe')}")
    settings = json.loads((BUNDLE / "config" / "settings.json").read_text(encoding="utf-8"))
    if not settings.get("attorney_short_name"):
        print("Fill in config/settings.json with the office details before the first filing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
