# Running on a Mac

The application itself is cross-platform. The document engine is pure Python,
the field schema knows nothing about filesystems, and the handful of places that
do care — where LibreOffice lives, how to open a folder, where config sits next
to a packaged app — are written for all three platforms and covered by
`tests/test_portability.py`.

**It has not been run on a Mac.** Everything here was developed and tested on
Windows. What follows is what to expect and where the friction is, not a report
of a successful macOS run.

## The short version

| | |
|---|---|
| Run from source | Straightforward. Install Python, `pip install -r requirements.txt`, go. |
| Word documents | Identical. `docxtpl` and `python-docx` are pure Python. |
| PDF export | Same as Windows: install LibreOffice, or go without. |
| Build a double-clickable app | Must be done **on a Mac**, and Gatekeeper will object the first time. |

The recommended route for one office on one Mac is **run from source**. It skips
every packaging problem below.

## Run from source

macOS ships a `python3` that is really a prompt to install Xcode command line
tools. Rather than fight it, install Python from
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
python build.py
```

It produces `dist/AdoptionFilingGenerator/AdoptionFilingGenerator.app`, with
`config/`, `templates/`, `output/` and `intake/` beside it — the same arrangement
as the Windows build, so a template is still dropped in without a rebuild.

### Gatekeeper will refuse it the first time

An app that is not signed and notarised by an Apple Developer account gets:

> "AdoptionFilingGenerator" cannot be opened because the developer cannot be
> verified.

This is expected, and it is not a sign anything is wrong. On the Mac it was built
on, right-click the app and choose **Open**, then **Open** again in the dialog;
macOS remembers the decision. If it was copied from another machine, macOS also
adds a quarantine flag, which is cleared with:

```bash
xattr -d com.apple.quarantine /path/to/AdoptionFilingGenerator.app
```

Removing this friction properly means enrolling in the Apple Developer Program
($99/year) and notarising the build. For a single office, running from source
avoids the whole question.

## What was actually checked

Verified by test, without a Mac:

- config and templates are looked for *beside* a `.app` bundle rather than buried
  inside it at `Contents/MacOS/` — the one real bug this review found;
- LibreOffice is looked for in the macOS location;
- generated file names avoid every character Windows forbids, which is a superset
  of what macOS dislikes, colons included;
- `os.startfile` — which exists only on Windows — is never imported at module
  level, and each platform has its own way to reveal the output folder;
- the interface asks for nothing over the network, and names a font stack that
  falls through to the macOS system font rather than Windows-only Segoe UI.

Not verified, because it needs a Mac:

- that pywebview opens a window and the native file dialogs behave;
- that PyInstaller produces a working `.app`;
- how the interface actually looks in SF Pro rather than Segoe UI.

If you get access to a Mac, `python -m pytest` is the first thing to run: 213
tests exercise everything except the window itself.
