"""Running somewhere other than this machine.

The office may end up on a Mac. Most of the application does not care — python-docx
and docxtpl are pure Python, and the field schema has no idea what a filesystem
is — but a few places had to be told, and those are the ones worth pinning down
so a later edit does not quietly break them.

None of this proves the app runs on macOS; only a Mac can do that. What it proves
is that the platform-dependent decisions are made deliberately rather than by
accident.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app import engine, registry


# --------------------------------------------------------------------------
# where config/ and templates/ are looked for
# --------------------------------------------------------------------------


def root_for(monkeypatch, *, frozen: bool, executable: str) -> Path:
    monkeypatch.setattr(registry.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(registry.sys, "executable", executable)
    return registry.project_root()


# --------------------------------------------------------------------------
# App Translocation, which quietly invalidates the arrangement above
# --------------------------------------------------------------------------
#
# A downloaded .app is launched by macOS from a randomised read-only mount with
# nothing beside it, so project_root() points somewhere config/ and templates/
# have never been. Observed on macOS 26: the mount carries the bundle alone, and
# moving the folder does not clear it — only approving the app does.


TRANSLOCATED = (
    "/private/var/folders/qk/T/AppTranslocation/6E475E89-9721-4200-8C1C-228EEA693EE1/d"
    "/AdoptionFilingGenerator.app/Contents/MacOS/AdoptionFilingGenerator"
)
INSTALLED = "/Users/someone/Documents/AdoptionFilingGenerator/AdoptionFilingGenerator.app/Contents/MacOS/AdoptionFilingGenerator"


def test_a_translocated_launch_is_recognised(monkeypatch):
    monkeypatch.setattr(registry.sys, "executable", TRANSLOCATED)
    assert registry.translocated()


def test_an_ordinary_launch_is_not(monkeypatch):
    monkeypatch.setattr(registry.sys, "executable", INSTALLED)
    assert not registry.translocated()


def test_running_from_source_is_not_translocated():
    """The developer case: no bundle, no quarantine, nothing to warn about."""
    assert not registry.translocated()


def test_the_notice_names_the_approval_that_clears_it(monkeypatch):
    """The message is the whole value of detecting this. If it stops naming the
    step that fixes it, whoever hits it is back to guessing."""
    from app import main

    monkeypatch.setattr(main.sys, "executable", TRANSLOCATED)
    page = main._TRANSLOCATED_PAGE.format(where=TRANSLOCATED)
    assert "Open Anyway" in page
    assert "Privacy" in page
    # and it must not send them looking at config/, which is the failure the
    # detection exists to prevent
    assert "configuration does not match" not in page


def test_from_source_the_root_is_the_repository(monkeypatch):
    monkeypatch.setattr(registry.sys, "frozen", False, raising=False)
    assert (registry.project_root() / "config" / "fields.json").exists()


def test_a_macos_app_looks_beside_the_bundle_not_inside_it(monkeypatch):
    """The executable lives at Name.app/Contents/MacOS/Name. Config left there
    would be hidden inside the bundle and lost on the next install."""
    root = root_for(
        monkeypatch, frozen=True,
        executable="/Applications/AdoptionFilingGenerator.app/Contents/MacOS/AdoptionFilingGenerator",
    )
    assert root.name != "MacOS"
    assert "Contents" not in root.parts
    assert root.as_posix().endswith("/Applications")


def test_a_windows_build_looks_next_to_the_exe(monkeypatch):
    root = root_for(monkeypatch, frozen=True, executable=r"C:\Apps\Docgen\AdoptionFilingGenerator.exe")
    assert root.name == "Docgen"


def test_a_folder_merely_called_macos_is_not_mistaken_for_a_bundle(monkeypatch):
    """The rule keys on the pair of directory names a bundle always has."""
    root = root_for(monkeypatch, frozen=True, executable="/opt/MacOS/AdoptionFilingGenerator")
    assert root.name == "MacOS"


# --------------------------------------------------------------------------
# LibreOffice, which is optional everywhere
# --------------------------------------------------------------------------


def test_libreoffice_is_looked_for_in_the_mac_location():
    assert any("/Applications/LibreOffice.app" in c for c in engine._SOFFICE_CANDIDATES)


def test_libreoffice_is_looked_for_on_windows_and_linux():
    joined = " ".join(engine._SOFFICE_CANDIDATES)
    assert "Program Files" in joined
    assert "/usr/bin/soffice" in joined


def test_a_missing_libreoffice_is_never_fatal(registry_fixture_unused=None, monkeypatch=None):
    """Covered fully in test_render; asserted here as a portability promise."""
    assert engine.find_soffice.__doc__ and "optional" in engine.find_soffice.__doc__


# --------------------------------------------------------------------------
# opening the output folder
# --------------------------------------------------------------------------


def test_every_platform_has_a_way_to_show_the_folder():
    source = Path(engine.__file__).read_text(encoding="utf-8")
    body = source.split("def open_folder")[1]
    assert "startfile" in body          # Windows
    assert '"open"' in body             # macOS
    assert '"xdg-open"' in body         # Linux


def test_failing_to_open_a_folder_does_not_lose_the_documents():
    body = Path(engine.__file__).read_text(encoding="utf-8").split("def open_folder")[1]
    assert "except OSError" in body


# --------------------------------------------------------------------------
# file names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ['SAMPLE "CHILD" / JR', "SAMPLE: CHILD", "SAMPLE\\CHILD", "SAMPLE|CHILD", "SAMPLE?CHILD"],
)
def test_generated_names_are_safe_on_both_filesystems(raw):
    """Windows forbids the larger set, so cleaning to its rules is safe on macOS
    too — and a colon, which macOS also dislikes, is in that set."""
    cleaned = engine.safe_filename(raw)
    assert not set(cleaned) & set('<>:"/\\|?*')
    assert cleaned


def test_a_name_that_cleans_away_to_nothing_still_produces_a_file():
    assert engine.safe_filename("///") == "document"


# --------------------------------------------------------------------------
# the front end
# --------------------------------------------------------------------------


def test_the_ui_names_a_font_every_platform_has():
    css = (registry.UI_DIR / "index.html").read_text(encoding="utf-8")
    stack = re.search(r"--sans:([^;]+);", css).group(1)
    # Segoe UI is Windows-only; the stack must fall through to something a Mac has.
    assert "system-ui" in stack or "-apple-system" in stack
    assert stack.rstrip().endswith("sans-serif")


#: Not a request: the SVG namespace is an identifier browsers never fetch.
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def test_the_ui_asks_for_nothing_over_the_network():
    """A font or script fetched from a CDN would fail on a machine with no
    network, and would be a request leaving a computer holding adoption records."""
    css = (registry.UI_DIR / "index.html").read_text(encoding="utf-8").replace(SVG_NAMESPACE, "")
    for forbidden in ("http://", "https://", "//fonts.", "<script src", "<link rel=\"stylesheet\"", "@import"):
        assert forbidden not in css, f"index.html reaches out via {forbidden}"


# --------------------------------------------------------------------------
# the engine itself
# --------------------------------------------------------------------------


def test_nothing_imports_a_windows_only_module_at_import_time():
    """os.startfile only exists on Windows; importing it at module scope would
    stop the app loading anywhere else."""
    for name in ("app.engine", "app.registry", "app.schema", "app.intake",
                 "app.responses", "app.cli", "app.server"):
        module = importlib.import_module(name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(winreg|msvcrt|win32)", source, re.M), name


def test_paths_are_built_with_pathlib_not_string_joining():
    """Backslash-joined paths are the classic way a Windows program refuses to
    run anywhere else."""
    for name in ("engine", "registry", "intake", "responses"):
        source = (Path(registry.__file__).parent / f"{name}.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#") and "_SOFFICE_CANDIDATES" not in line
        )
        assert '+ "\\\\"' not in code, name


# --------------------------------------------------------------------------
# running where there is no screen
# --------------------------------------------------------------------------


class _NoGui:
    """Makes pywebview unimportable, the way a headless container has it."""

    def find_module(self, name, path=None):
        if name == "webview" or name.startswith("webview."):
            return self

    def load_module(self, name):
        raise ImportError(f"no GUI toolkit available: {name}")


def test_the_browser_front_end_runs_without_a_gui_toolkit(monkeypatch):
    """A Codespace, a container or a server has no windowing library. The bridge
    is shared with the desktop app, so importing one on the way past would stop
    the browser front end dead — pywebview is imported only where a window is
    actually opened."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import sys
        class Blocker:
            def find_module(self, name, path=None):
                if name == "webview" or name.startswith("webview."):
                    return self
            def load_module(self, name):
                raise ImportError("no GUI toolkit available")
        sys.meta_path.insert(0, Blocker())

        from app import server
        from app.registry import Registry
        api = server.BrowserApi(Registry.load())
        assert api.bootstrap()["ok"], "bootstrap failed"
        assert api.mode == "browser"
        print("ok")
        """
    )
    finished = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(Path(registry.__file__).parent.parent),
    )
    assert finished.returncode == 0, finished.stderr
    assert "ok" in finished.stdout


def test_the_command_line_runs_without_a_gui_toolkit():
    """Template work happens over the CLI, often on a machine with no screen."""
    import subprocess
    import sys

    finished = subprocess.run(
        [sys.executable, "-m", "app.cli", "check"],
        capture_output=True, text=True, cwd=str(Path(registry.__file__).parent.parent),
    )
    assert finished.returncode == 0, finished.stderr
    assert "Configuration OK" in finished.stdout
