# Adoption Filing Generator

Local desktop app that generates Oklahoma adoption court filings from a single
intake form. Replaces find-and-replace on `XXX` plus manual deletion of the
paragraphs that don't apply.

## Status

Working end to end for all six variants — four DHS combinations and two
step-parent — across thirteen templates. Three of those are filed only when the
matter calls for it, and the intake asks: concurrent jurisdiction, the juvenile
records request, and the filing cover sheets. Pick a type, fill in the intake, review,
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
Those details are edited in the application itself — **Office details** in the
sidebar — rather than by hand, though the file is still ordinary JSON.

The attorney's signature image is kept the same way, as `config/signature.<ext>`.
Upload it once under **Office details**; it is printed on the attorney's own
signature lines and nowhere else, and any filing can be generated without it by
clearing "Place the attorney's signature" on the Review screen. It is gitignored
and preserved across rebuilds, like the settings beside it.

A packaged build also carries a copy of `config/` and `templates/` *inside* the
executable. Those are a fallback, never the working copy: they are used only when
there are none beside the application, which happens when somebody moves the
`.app` on its own or macOS relocates it. In that case the application seeds a
per-user folder from them and says where it put it. See `docs/MACOS.md`.

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
- **Paper.** Export the blank questionnaire, email it, and type the answers
  back in.
- **An interview.** Export the intake worksheet — every question this matter
  asks, including the ones the family is never asked — take it into the meeting
  and type it up afterwards.

Both print on **one page**, laid out the way the office's own intake sheet
always was: two questions to a line, each on a rule you can write along, section
headings in capitals. Both are built from `config/fields.json`, so a question
added there appears on whichever of them it belongs to. A question that is only
asked when a box is ticked carries a dagger, with one footnote explaining it —
a blank on paper is otherwise ambiguous between "not asked" and "answered no".

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
python -m app.cli render dhs dhs_1p_1c --random # a made-up test matter, different each run
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

### Install it somewhere short

`dist/` is where a build lands, not where the application should live. Copy the
whole `AdoptionFilingGenerator` folder somewhere near the root of the drive:

```
C:\AdoptionFilingGenerator\
```

Windows cannot open a file whose full path reaches 260 characters, and it is
*Word* that refuses, not this application — so a document written past the limit
looks generated and cannot be opened. Running from `dist/` inside a synced
OneDrive folder spends 170 characters before the file name even starts, which is
enough to trip it.

The app shortens names to fit rather than leaving that to be discovered, and says
so when it does. But a short install path means it never has to, and the files
keep the names the configuration gives them.

## Windows and macOS

The application is cross-platform and the platform-dependent decisions are
covered by `tests/test_portability.py`. It has only been *run* on Windows. See
`docs/MACOS.md` for what to expect on a Mac — running from source is
straightforward and needs nothing installed by hand beyond Python itself.

## PDF export

Optional, via LibreOffice. If `soffice` is not on the machine, the checkbox is
disabled with an explanation and .docx generation is unaffected.

## Showing it to someone

**Fill with test data** on the intake screen invents a whole matter in one click:
every question answered, ready to generate. Press it again for a different one —
the dates, the county and every yes/no answer change, so successive runs read
*different paragraphs* rather than the same one with different names.

Every name carries a SAMPLE prefix. That is not fussiness: what comes out is
otherwise indistinguishable from a real filing sitting in the output folder.

The same thing from the command line is `--random`, with `--seed` to reproduce a
particular one.

## Drafts, and the "fail loud" rule

PROJECT_BRIEF.md says a missing variable must stop generation, because "a document
that renders with a blank where a name should be is worse than a document that
fails to render". That reasoning is sound and the rule still holds — but staff
also need to take an unfinished petition away and work on it.

Both are satisfied by never leaving a blank:

- An unanswered question does not block. It prints as `[ Child — Date of birth ]`,
  naming the question a person can go and answer.
- Every file of an unfinished filing is named `DRAFT - …`, in a folder named
  `DRAFT_…`, so it cannot be mistaken for something ready to file.
- The marker names the *answerable* field, not the internal one: nobody types
  `child1_birth_day`, they type the date of birth it is derived from.
- An answer that is **wrong** — a date that is not a date, an option that is not on
  the list — still refuses. Absent is forgiven; wrong is not, because wrong puts
  nonsense in a filing rather than a visible gap.

`tests/test_drafts.py` holds this line, including a check that every variant can be
drafted from a completely empty intake with no template tag left behind.

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
