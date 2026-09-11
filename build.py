"""Package the application for the machine you run this on.

    python build.py
    python build.py --install "C:/AdoptionFilingGenerator"

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

#: Windows cannot open a path this long or longer, and it is Word that refuses
#: rather than this application — so where the app is installed decides whether
#: documents it writes can be opened. See app/engine.py.
PATH_LIMIT = 259

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
#: office's own attorney details, so an existing copy is carried across. Read and
#: written as bytes: config/signature.* is the attorney's signature image, and
#: losing it to a rebuild would mean re-scanning a signature to get it back.
PRESERVE = ("config/settings.json",)

#: Same rule, for files whose name is not known ahead of time — the signature is
#: stored under whatever extension the image turned out to be.
PRESERVE_GLOBS = ("config/signature.*",)

#: Packages PyInstaller does not always find on its own, and the check that they
#: made it in. pypdf is imported inside a function — only when a client's form
#: arrives as a PDF — and PyInstaller's scan missed it, so the packaged app was
#: built without it and would have failed at the moment someone used the feature.
#: docx and docxtpl are here for their *data*, not their code. python-docx reads
#: docx/templates/default-header.xml and half a dozen siblings off disk when it
#: assembles a document — files no import ever touches, so a build missing them
#: passes every check that only imports and then fails at Generate, on somebody
#: else's computer, with a path they have no way to interpret.
COLLECT = ("webview", "pypdf", "docx", "docxtpl")

#: The built application checks its own dependencies; see main.self_check. Asking
#: the .exe whether it can import what it needs beats inspecting the folder, which
#: only sees packages PyInstaller happened to leave loose — the pure-Python ones
#: live inside the archive and look missing from the outside.


def run_pyinstaller(universal: bool = False) -> None:
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",                      # no console window behind the app
        "--name", NAME,
        "--add-data", f"{ROOT / 'app' / 'ui'}{separator}ui",
        # A second copy of config/ and templates/, carried inside the
        # application. The copies *beside* it remain the ones the office edits
        # and the ones a rebuild preserves; these are only reached when there
        # are none beside it — an .app dragged out of the folder it arrived in,
        # or one macOS has relocated. Without them that situation is an
        # application that reports every one of its own templates missing,
        # which is how the first person to install this on a second Mac spent
        # their afternoon.
        "--add-data", f"{ROOT / 'config'}{separator}defaults/config",
        "--add-data", f"{ROOT / 'templates'}{separator}defaults/templates",
        *[argument for package in COLLECT for argument in ("--collect-all", package)],
        "--distpath", str(STAGING),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
    ]
    if universal:
        # A Mac build is otherwise thin: it carries only the architecture of the
        # machine it was built on, and an Apple Silicon build will not launch at
        # all on an Intel Mac. universal2 carries both, which matters when the
        # people receiving it do not all have the same vintage of Mac.
        #
        # Every compiled dependency must itself be universal2 or PyInstaller
        # fails here; see arch_report(), which checks the result rather than
        # trusting the flag.
        command += ["--target-arch", "universal2"]
    command.append(str(ROOT / "app" / "main.py"))
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


#: rmtree's error callback was renamed in 3.12: onerror before, onexc after, and
#: passing the wrong one is a TypeError rather than something ignored. Both hand
#: the callback (func, path, third), so _clear_readonly serves as either. This
#: only shows on a rebuild over an existing installation — a first build removes
#: nothing — which is why it survives a fresh build on a newer Python.
_RMTREE_CALLBACK = "onexc" if sys.version_info >= (3, 12) else "onerror"


def _remove(path: Path) -> None:
    """Delete a program file or folder, explaining the usual cause of failure."""
    try:
        if path.is_dir():
            shutil.rmtree(path, **{_RMTREE_CALLBACK: _clear_readonly})
        elif path.exists():
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
    except (PermissionError, OSError) as exc:
        raise SystemExit(
            f"Cannot replace {path}: {exc}.\n"
            f"Close the application if it is running, let OneDrive finish syncing, "
            f"and run this again."
        ) from exc


def _program_files() -> list[Path]:
    """What PyInstaller produced that belongs in the installation.

    A windowed macOS build writes two things side by side: the collected program
    directory, and the Name.app bundle that holds a second copy of it. The bundle
    is the whole application — it is what a person double-clicks — so on a Mac it
    is the only thing installed, and the loose directory beside it is left behind.
    Copying both would install the program twice.
    """
    if MAC:
        app = STAGING / f"{NAME}.app"
        if app.exists():
            return [app]
        # An older PyInstaller, or a build that somehow produced no bundle: fall
        # back to the loose program so the build still yields something runnable.
        return list((STAGING / NAME).iterdir())
    return list((STAGING / NAME).iterdir())


#: What Apple's architecture names mean to someone deciding whether an app will
#: run on their machine.
_ARCH_NAMES = {"arm64": "Apple Silicon", "x86_64": "Intel"}


def _archs(path: Path) -> set[str]:
    """The architectures in one Mach-O file, or an empty set if it isn't one."""
    result = subprocess.run(["lipo", "-archs", str(path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return set()
    return set(result.stdout.split())


def arch_report() -> str | None:
    """Which Macs the built application will actually launch on.

    --target-arch is a request, not a result: if one compiled dependency ships
    thin, PyInstaller narrows the build to match and the flag is still on the
    command line. So read it back off the program and every compiled library
    inside it, and report the *intersection* — an app is only as universal as
    its narrowest piece, and a missing architecture surfaces as a launch
    failure on the recipient's machine rather than here.
    """
    if not MAC:
        return None
    program = built_executable()
    if not program.exists():
        return None

    common = _archs(program)
    for library in (*program.parent.parent.rglob("*.so"),
                    *program.parent.parent.rglob("*.dylib")):
        found = _archs(library)
        if found:
            common &= found
    if not common:
        return "could not determine which architectures this build supports"
    return ", ".join(sorted(_ARCH_NAMES.get(a, a) for a in common))


def install_program() -> None:
    """Copy the freshly built program over the installation, and nothing else."""
    BUNDLE.mkdir(parents=True, exist_ok=True)

    for item in _program_files():
        target = BUNDLE / item.name
        _remove(target)
        if item.is_dir():
            shutil.copytree(item, target, symlinks=True)
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


def read_preserved() -> dict[str, bytes]:
    """Take a copy of the installation's own settings before anything is deleted.

    Read at the very start of the build, not just before the delete that would
    destroy it: shutil.rmtree removes files depth-first and can fail partway, so a
    build that dies while clearing config/ can leave the folder there with the
    settings already gone.
    """
    kept: dict[str, bytes] = {}
    for relative in PRESERVE:
        existing = BUNDLE / relative
        if existing.exists() and existing.read_bytes().strip():
            kept[relative] = existing.read_bytes()
    for pattern in PRESERVE_GLOBS:
        for existing in sorted(BUNDLE.glob(pattern)):
            if existing.is_file() and existing.stat().st_size:
                kept[existing.relative_to(BUNDLE).as_posix()] = existing.read_bytes()
    return kept


def copy_alongside(kept: dict[str, bytes]) -> None:
    for name in ALONGSIDE:
        target = BUNDLE / name
        _remove(target)
        shutil.copytree(
            ROOT / name, target,
            ignore=shutil.ignore_patterns("_source", "~$*", "*.doc", "__pycache__"),
        )
        print(f"  copied {name}/")

    for relative, content in kept.items():
        target = BUNDLE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print(f"  kept the installation's own {relative}")

    for name in KEEP_DIRS:
        directory = BUNDLE / name
        existing = len(list(directory.rglob("*"))) if directory.exists() else 0
        directory.mkdir(parents=True, exist_ok=True)
        print(f"  left {name}/ alone ({existing} item(s))" if existing else f"  created empty {name}/")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="build.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install", metavar="PATH", type=Path,
                        help="install here instead of into dist/, keeping this copy's "
                             "own settings.json, output/ and intake/")
    parser.add_argument("--universal", action="store_true",
                        help="macOS only: build one application that runs on both "
                             "Apple Silicon and Intel Macs. Slower and larger; use it "
                             "when you do not know which Macs will receive it.")
    arguments = parser.parse_args(argv)
    destination = arguments.install
    if arguments.universal and not MAC:
        print("--universal is a macOS option; a Windows build has one architecture.",
              file=sys.stderr)
        return 2
    if destination is not None:
        # Put the build where it will actually be run from, rather than into dist/
        # and out again. dist/ is a build artifact; an installation is not, and
        # copying through one only invites a lock on a file nobody needs.
        global BUNDLE
        BUNDLE = destination.resolve()

    if not (ROOT / "config" / "matters.json").exists():
        print("Run this from the repository root.", file=sys.stderr)
        return 2

    kept = read_preserved()

    run_pyinstaller(universal=arguments.universal)
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
    print("  self-check: every dependency present, and the built application "
          "generated a document")

    print("")
    print(f"Built {built_executable()}")
    runs_on = arch_report()
    if runs_on:
        print(f"  runs on: {runs_on}")
        if MAC and "Intel" not in runs_on:
            print("  an Intel Mac cannot open this build; rebuild with --universal "
                  "if one might receive it.")
    settings = json.loads((BUNDLE / "config" / "settings.json").read_text(encoding="utf-8"))
    if not settings.get("attorney_short_name"):
        print("No office details yet: open the app and fill in Office details "
              "before the first filing.")

    room = PATH_LIMIT - len(str(BUNDLE / "output"))
    print(f"  its output folder leaves {room} characters for folder and file names")
    if room < 90:
        print("  that is tight; somewhere like C:/AdoptionFilingGenerator leaves far more.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
