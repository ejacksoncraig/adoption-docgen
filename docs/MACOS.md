# Running on a Mac

The application itself is cross-platform. The document engine is pure Python,
the field schema knows nothing about filesystems, and the handful of places that
do care — where LibreOffice lives, how to open a folder, where config sits next
to a packaged app — are written for all three platforms and covered by
`tests/test_portability.py`.

**It has now been run on a Mac** — macOS 26.3, Apple Silicon, Python 3.9.6 — from
source and as a built `.app`. What follows is a report of that, not a prediction.
Everything was originally developed on Windows.

## The short version

| | |
|---|---|
| Run from source | Straightforward. `pip install -r requirements.txt`, go. |
| Word documents | Identical. `docxtpl` and `python-docx` are pure Python. |
| The window | pywebview draws it under WebKit. Confirmed working. |
| PDF export | Same as Windows: install LibreOffice, or go without. |
| Build a double-clickable app | Must be done **on a Mac**. Gatekeeper will object the first time. |
| Sending it to someone else | `python build.py --universal`, then see `INSTALL.md`. |

For one person working on the code, **run from source**. To hand the program to
somebody who does not have Python, build the `.app`.

## Run from source

The `python3` that ships with macOS is often described as a stub that only
prompts for the Xcode command line tools. Once those tools are installed it is a
real framework build of Python 3.9, and the whole dependency set installs against
it without complaint — including the pyobjc pieces pywebview needs. Check before
assuming you need anything else:

```bash
python3 --version
```

If that answers, use it. Otherwise install Python from
[python.org](https://www.python.org/downloads/macos/) — one normal installer, no
Homebrew, no developer account.

```bash
cd adoption-docgen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

`pip install` pulls the Mac pieces automatically: pywebview declares
`pyobjc-core`, `pyobjc-framework-Cocoa` and `pyobjc-framework-WebKit` for macOS
only, and they arrive without being asked for. There is nothing to install by
hand.

This is one place a Mac is *easier* than Windows. The window is drawn with
WebKit, which is part of macOS. On Windows the same job needs the WebView2
runtime, which is a separate Microsoft component.

## PDF export

Optional on every platform. Install LibreOffice from
[libreoffice.org](https://www.libreoffice.org/download/) and the app finds it at
`/Applications/LibreOffice.app/Contents/MacOS/soffice`. Without it, the PDF
checkbox is disabled with an explanation and .docx generation is unaffected.

## Building a double-clickable app

Only worth doing if someone other than you has to launch it.

**PyInstaller cannot cross-compile.** A Mac app has to be built on a Mac; the
Windows `.exe` in `dist/` is of no use there. On the Mac, the same command works:

```bash
python build.py              # for this Mac
python build.py --universal  # for any Mac
```

It produces `dist/AdoptionFilingGenerator/AdoptionFilingGenerator.app`, with
`config/`, `templates/`, `output/` and `intake/` beside it — the same arrangement
as the Windows build, so a template is still dropped in without a rebuild.

### Which Macs the result runs on

A plain build is *thin*: it carries only the architecture of the machine that
built it, and an Apple Silicon build **will not launch at all** on an Intel Mac.
That is invisible on the machine it was built on and only shows up on the
recipient's.

`--universal` builds for both. It requires every compiled dependency to ship a
universal2 wheel; at the time of writing `lxml` does and `MarkupSafe` does so
only for some releases, so if a build fails here, that is where to look:

```bash
pip install --force-reinstall --no-deps \
  "MarkupSafe-3.0.2-cp39-cp39-macosx_10_9_universal2.whl"
```

Either way the build reports what it produced — `runs on: Apple Silicon, Intel`
— by reading the architectures back off the binary and every compiled library
inside the bundle, rather than trusting the flag it was given. An app is only as
universal as its narrowest piece.

The Intel half can be smoke-tested from an Apple Silicon Mac through Rosetta:

```bash
arch -x86_64 dist/AdoptionFilingGenerator/AdoptionFilingGenerator.app/Contents/MacOS/AdoptionFilingGenerator --self-check
```

### Packaging it to send

Use `ditto`, not `zip`. A `.app` contains framework symlinks that `zip -r`
flattens into duplicate copies, which can break the signature:

```bash
cd dist && ditto -c -k --sequesterRsrc --keepParent \
    AdoptionFilingGenerator AdoptionFilingGenerator-macOS.zip
```

Hand the recipient `docs/INSTALL.md` with it — a copy travels inside the folder
as `READ ME FIRST.md`.

### Gatekeeper will refuse it the first time

An app that is not signed and notarised by an Apple Developer account gets:

> "AdoptionFilingGenerator" cannot be opened because the developer cannot be
> verified.

This is expected, and it is not a sign anything is wrong. `spctl -a -vv` on a
downloaded copy reports `rejected`, which is Gatekeeper working as designed on an
ad-hoc-signed build.

To allow it, once: **System Settings** → **Privacy & Security** → scroll to
**Security**, where a line names the blocked app with an **Open Anyway** button.
macOS then remembers.

Older instructions say to right-click the app and choose **Open** instead. That
worked up to macOS 14; **Apple removed the shortcut in macOS 15 (Sequoia)**, so
on any current Mac the Privacy & Security route is the one that works.

Either way, a copy that arrived from another machine carries a quarantine flag,
which can also be cleared directly:

```bash
xattr -d com.apple.quarantine /path/to/AdoptionFilingGenerator.app
```

Removing this friction properly means enrolling in the Apple Developer Program
($99/year) and notarising the build. For a single office, the one-time Open
Anyway avoids the whole question.

## What was actually checked

Confirmed by running it on macOS 26.3, Apple Silicon:

- the full suite passes — 253 passed, 14 skipped. The skips are the Windows
  260-character path limit, which does not apply, and one PDF test with no
  LibreOffice installed;
- `python -m app.main` opens a pywebview window under WebKit;
- `python -m app.server` serves the same app to a browser on loopback;
- the CLI renders all six variants with no placeholder left behind;
- `python build.py --universal` produces a `.app` that launches as its own
  application, finds `config/` and `templates/` beside the bundle from a fresh
  location, and passes its own `--self-check` on **both** architectures — the
  x86_64 half verified through Rosetta;
- the whole dependency set, pyobjc included, installs against the system
  Python 3.9.

Two tests fail on macOS, and both are the test's fault rather than the
program's. Each hands a literal Windows path string to `pathlib`, which on a
POSIX system does not treat `\` as a separator:

- `test_a_windows_build_looks_next_to_the_exe` parses `C:\Apps\Docgen\...exe`;
- `test_an_upload_keeps_only_the_file_name` expects `..\..\windows\evil.csv` to
  be stripped to its last component. Real traversal with forward slashes *is*
  stripped on macOS, and a backslash is a legal character in a macOS file name,
  so this is a naming wart and not a path-traversal hole.

Still not verified:

- the native Save and Open dialogs, beyond the window itself appearing;
- how the interface looks in SF Pro rather than Segoe UI;
- a real Intel Mac, as opposed to the x86_64 slice under Rosetta.
