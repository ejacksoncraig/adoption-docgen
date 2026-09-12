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

### Asking an installation what it is using

Supporting a copy on somebody else's Mac otherwise means asking them to guess:

```bash
/path/to/AdoptionFilingGenerator.app/Contents/MacOS/AdoptionFilingGenerator --where
```

It prints whether macOS has relocated it, whether there is a copy of `config/`
and `templates/` beside it, where the per-user fallback is, and which of the two
is actually in use. Every question that has come up so far is answered by that
list.

### Packaging it to send

Use `ditto`, not `zip`. A `.app` contains framework symlinks that `zip -r`
flattens into duplicate copies, which can break the signature:

```bash
cd dist && ditto -c -k --sequesterRsrc --keepParent \
    AdoptionFilingGenerator AdoptionFilingGenerator-macOS.zip
```

Hand the recipient `docs/INSTALL.md` with it — a copy travels inside the folder
as `READ ME FIRST.md`.

## Distributing it properly: Developer ID

Everything below about Gatekeeper, approving the app and App Translocation is
what happens to an **unsigned** build. A signed, notarised, stapled one never
enters any of it: it opens on a machine that has never seen it before, with no
prompt and no translocation, which matters here more than it looks — under
translocation the `.app` is mounted alone and `config/` and `templates/` are
left behind, and those live beside the bundle by design.

This needs three things that are not in this repository: an Apple Developer
Program membership, a **Developer ID Application** certificate installed in the
login keychain, and a notarytool credential profile.

    # 1. confirm the certificate is installed
    security find-identity -v -p codesigning
    #    look for: "Developer ID Application: Your Name (TEAMID)"

    # 2. store an App Store Connect credential once, by name
    xcrun notarytool store-credentials adoption-docgen \
        --apple-id you@example.com --team-id TEAMID \
        --password <app-specific-password>

    # 3. build, sign, notarise and staple in one go
    python build.py --universal --notarize adoption-docgen

`--sign` on its own signs and verifies without sending anything to Apple, which
is the faster loop while getting the entitlements right. `build.py` refuses with
an explanation rather than a codesign error when no certificate is installed, or
when more than one Developer ID is present and it cannot tell which you meant.

### Why the entitlements exist

`packaging/entitlements.plist` grants two exceptions, both required by the
hardened runtime that notarisation insists on, and neither optional for a Python
application: `disable-library-validation`, because PyInstaller dlopen()s dozens
of `.so` files that are not signed by this team, and
`allow-unsigned-executable-memory`, because CPython writes and executes memory
for its own bytecode. Nothing else is granted — the application opens no sockets
and makes no outbound request.

### What to hand over

The whole `AdoptionFilingGenerator` folder, not the `.app` alone: `config/` and
`templates/` sit beside it and the office edits them in place. Notarisation
covers the `.app`; the data beside it is unsigned, which is correct — it is
meant to be edited.

Stapling is the step people skip. Without it the notarisation ticket is only
available online, and the first launch on a machine with no network is refused.
`build.py --notarize` staples.

### Gatekeeper will refuse it the first time

An app that is not signed and notarised by an Apple Developer account gets:

> "AdoptionFilingGenerator" cannot be opened because the developer cannot be
> verified.

On macOS 26 the wording is *"Apple could not verify ... is free of malware that
may harm your Mac or compromise your privacy."* This is expected, and it is not a
sign anything is wrong. `spctl -a -vv` on a downloaded copy reports `rejected`,
which is Gatekeeper working as designed on an ad-hoc-signed build.

To allow it, once: **System Settings** → **Privacy & Security** → scroll to
**Security**, where a line names the blocked app with an **Open Anyway** button.
macOS then remembers.

Older instructions say to right-click the app and choose **Open** instead. That
worked up to macOS 14; **Apple removed the shortcut in macOS 15 (Sequoia)**, so
on any current Mac the Privacy & Security route is the one that works.

### App Translocation, which this design is unusually exposed to

Skipping the approval above does not merely leave the app blocked. macOS runs a
still-quarantined app through **App Translocation**: the bundle is mounted alone
at a randomised read-only path and launched from there.

That is ordinarily harmless. It is not harmless here, because `config/` and
`templates/` live *beside* the bundle by design — under translocation they are
not beside anything:

```
$ mount | grep translocation
/Users/x/Documents/AdoptionFilingGenerator/AdoptionFilingGenerator.app on
  /private/var/folders/.../AppTranslocation/2D77A8BD-.../ (nullfs, read-only, nobrowse)

$ ls /private/var/folders/.../AppTranslocation/2D77A8BD-.../d
AdoptionFilingGenerator.app          # and nothing else
```

The mount is read-only, so `output/` and `intake/` cannot be written either. The
symptom is a configuration error listing files that are present and correct on
disk, which sends whoever hit it hunting through `config/` for a fault that is
not there.

This is no longer fatal. `build.py` packs a second copy of `config/` and
`templates/` *inside* the bundle, and `registry.data_root()` falls back to a
per-user folder — `~/Library/Application Support/Adoption Filing Generator` —
seeded from them on first run. The copies beside the application still win when
they are there, so the office's own edited templates are used and a rebuild
preserves them; the fallback only catches the case where there is nothing beside
the application at all.

That covers both ways this went wrong on somebody else's Mac: a translocated
launch, and an `.app` dragged out of the folder it arrived in. `registry.translocated()`
still reports the first as a note on the opening screen, because a relocated app
cannot see files kept beside it and the approval is worth doing. Covered by
`tests/test_portability.py`.

Two things established by testing on macOS 26, both contrary to what is usually
written about this:

- **Moving the folder does not clear it.** Finder-moving the enclosing folder
  from `Downloads` to `Documents` leaves the quarantine flag on the bundle, and
  the next launch is translocated again.
- **Moving the `.app` itself does not clear it either**, and would break the
  config-beside-the-bundle arrangement even if it did.

Approving the app — or `xattr -dr com.apple.quarantine` on the folder — is what
actually clears it. Verified: after clearing, the app launches from its real path
with no translocation mount.

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
