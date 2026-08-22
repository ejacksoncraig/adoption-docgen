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

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running

```bash
python -m app.main        # the desktop window
```

The same engine is reachable from the command line, which is how template work
gets done:

```bash
python -m app.cli check                         # validate config/ against templates/
python -m app.cli fields dhs dhs_1p_1c          # the intake form for a variant
python -m app.cli render dhs dhs_1p_1c          # render with placeholder answers
python -m app.cli render dhs dhs_1p_1c --falsy  # ...and the other side of every branch
python -m app.cli render dhs dhs_1p_1c --intake intake/saved.json
python -m app.cli questionnaire dhs dhs_1p_1c   # blank .docx to email to the family
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

Produces `dist/AdoptionFilingGenerator/` containing the .exe plus `config/` and
`templates/` **beside** it, not inside it — so a new adoption type is still a
matter of dropping in a .docx and editing two JSON files, with no rebuild.

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
