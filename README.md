# Adoption Filing Generator

Local desktop app that generates Oklahoma adoption court filings from a single
intake form. Replaces find-and-replace on `XXX` plus manual deletion of the
paragraphs that don't apply.

## Status

Working end to end for all six variants — four DHS combinations and two
step-parent — across nine templates. Pick a type, fill in the intake, review,
generate.

Depth of testing varies. `dhs_petition_decree_1p_1c` has a golden file and
per-branch assertions; the step-parent petitions have per-branch assertions for
every consent basis; the rest are covered only by a smoke check that they render
with no placeholder left behind. See "Coverage as it stands" in
`docs/TEMPLATE_AUTHORING.md`.

Before the first real filing, fill in `config/settings.json` — the attorney
details. Generation refuses to run without them rather than printing a decree
with no attorney of record.

## Where config lives

`config/` and `templates/` in the repository are the source of truth. The copies
inside `dist/AdoptionFilingGenerator/` are a *build output* — `python build.py`
regenerates them, and they are gitignored, so edits made there are not saved and
will be overwritten. Edit the repository copies, then rebuild.

The one exception is `config/settings.json`: the office's own attorney details
belong to the installation, so a rebuild keeps whatever is already in the bundle.

## Start here

Read `PROJECT_BRIEF.md`. Then `config/fields.json`, `config/matters.json`, and
`docs/TEMPLATE_AUTHORING.md`.

## Getting a client's answers in

Two ways, neither of which needs a server or a login:

- **They already filled in a form.** Load the responses as a CSV export, or the
  response as a PDF — the app finds each question, matches it to a field, shows
  you the match to confirm, and fills in the intake. A CSV reads exactly; a PDF
  has to be inferred, so check the mapping the first time. See
  `docs/CLIENT_FORMS.md`.
- **Paper.** Export a blank .docx questionnaire, email it, and type the answers
  back in.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running

```bash
python -m app.main        # the desktop window
python -m app.server      # the same app in a browser on this machine
```

Both run the identical engine and write to the same `output/` and `intake/`
folders — `app/server.py` reuses the desktop bridge, so the two front ends cannot
drift apart. The browser is the easier one to work on: edit `app/ui/index.html`
and press refresh, with no rebuild and no window restart.

Two differences, because a web page cannot open a native Save or Open box:
saving an intake writes to `intake/` under its usual name and tells you where,
and opening a file uses the browser's own picker.

The socket listens on `127.0.0.1` only, and every call must carry a token the
program hands to its own page, so another site you have open cannot reach it.
That makes it safe to run on your own machine; it does **not** make it safe to
expose. See the confidentiality note below.

## Trying it from a link

`.devcontainer/` is set up so **GitHub Codespaces** runs the real application in
a container off this repository and forwards its port — a browser tab, no rewrite,
same engine. Use it with the SAMPLE answers only: a Codespace is GitHub's machine,
not the office's. See `docs/CODESPACES.md`.

GitHub Pages cannot host this. Pages serves static files and never runs code on a
server, and this application's engine is Python from end to end.

The same engine is reachable from the command line, which is how template work
gets done:

```bash
python -m app.cli check                         # validate config/ against templates/
python -m app.cli fields dhs dhs_1p_1c          # the intake form for a variant
python -m app.cli render dhs dhs_1p_1c          # render with placeholder answers
python -m app.cli render dhs dhs_1p_1c --falsy  # ...and the other side of every branch
python -m app.cli render dhs dhs_1p_1c --intake intake/saved.json
python -m app.cli questionnaire dhs dhs_1p_1c   # blank .docx to email to the family
python -m app.cli import-form dhs dhs_1p_1c responses.csv   # a client's completed form
python -m app.cli import-form dhs dhs_1p_1c response.pdf    # ...or as a PDF
```

`check` is worth running after any edit to `config/` or `templates/`. It reports
every problem at once — undeclared variables, misspelled tokens, missing files,
fields a variant does not collect — and it is the same validation the app runs at
launch.

## Tests

```bash
python -m pytest
```

Covers both sides of every conditional in the pilot template, a golden file of
the rendered text, and the assertion that no generated document contains `XXX`,
`{{` or `{%`. When a template change is intentional:

```bash
python -m tests.test_golden --update    # then read the diff in the commit
```

## Packaging

```bash
python build.py
```

Produces `dist/AdoptionFilingGenerator/` containing the application plus
`config/` and `templates/` **beside** it, not inside it — so a new adoption type
is still a matter of dropping in a .docx and editing two JSON files, with no
rebuild. The build then runs the packaged app's own `--self-check`, which imports
every dependency and fails the build if one did not make it in.

PyInstaller cannot cross-compile: a Windows `.exe` must be built on Windows and a
macOS `.app` on a Mac.

## Windows and macOS

The application is cross-platform and the platform-dependent decisions are
covered by `tests/test_portability.py`. It has only been *run* on Windows. See
`docs/MACOS.md` for what to expect on a Mac — running from source is
straightforward and needs nothing installed by hand beyond Python itself.

## PDF export

Optional, via LibreOffice. If `soffice` is not on the machine, the checkbox is
disabled with an explanation and .docx generation is unaffected.

## Confidentiality

This repository must contain **blank templates only**. Generated documents,
saved intake files, and any document containing a real name, date of birth, or
case number are gitignored and must stay that way. Git history is permanent;
a client record committed once is committed forever.

The application makes no network request of any kind, stores nothing in the
browser view, and writes only to `output/` and `intake/`.

`python -m app.server` is not an exception to that: the socket is bound to
loopback, which nothing outside this computer can reach, and the documents are
still rendered by Python on this machine. Putting this on a network — even the
office LAN — means authentication, transport security, access logging and a
security review first, and is a separate piece of work.
