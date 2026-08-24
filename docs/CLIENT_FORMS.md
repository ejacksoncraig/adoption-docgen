# Importing a form the client filled in

Instead of retyping a family's answers, load the form they already completed. The
app finds each question, matches it to an intake field, shows you the match to
confirm, and fills in the form.

Retyping is where a misspelled name enters a petition. This removes that step
without letting the computer decide anything on its own.

## Which file to use

Two kinds work, and it is worth knowing which is which:

| File | How well it reads |
|---|---|
| **Spreadsheet export (.csv)** | Exact. The columns are labelled and the answers are separated for you. Use this when you can. |
| **PDF** | Good, but inferred. A PDF has no columns — just text in an order — so the app has to work out where each answer starts and stops. |
| **Fillable PDF** | Exact. If the PDF has real form fields, their values are read straight out. |
| **Scanned or photographed PDF** | Cannot be read at all. There is no text in the file, only a picture of one. The app says so rather than importing nothing. |

A .csv is the more reliable route. Reach for the PDF when that is all you have.

## What to send Google

Any form tool works as long as it exports a spreadsheet. In Google Forms:

1. Open the form → **Responses** → the green Sheets icon to open the responses
   in Google Sheets.
2. In Sheets: **File → Download → Comma-separated values (.csv)**.

That file is what you load. It has one row per family who filled the form in,
and one column per question.

To use a PDF instead: open the response in Google Forms and print it
(**Ctrl+P → Save as PDF**), or use whatever PDF the family sent you.

## Loading it

1. Pick the adoption type and the combination of petitioners and children first —
   the app needs to know which fields exist before it can match anything, and for
   a PDF it needs to know which questions to look for.
2. **Import a form the client filled in…** and choose the .csv or .pdf.
3. Check the mapping screen (below), then **Import these answers**.
4. Fill in the rest: the filing county, the case number, the fee summary, and
   anything the family left blank. The app lists exactly what is outstanding.

Answers that came from the client are marked **from client** on the intake form,
so you can see at a glance which boxes are yours.

From the command line:

```bash
python -m app.cli import-form dhs dhs_1p_1c responses.csv
python -m app.cli import-form dhs dhs_1p_1c response.pdf
python -m app.cli import-form dhs dhs_1p_1c responses.csv --row 2 --save intake/smith.json
```

## The mapping screen

Every column of the export is listed with the client's answer beside it and the
field it will fill. Some rows are flagged:

| Flag | What it means |
|---|---|
| *check* | Matched on a close-enough resemblance. Read it before importing. |
| *which one?* | The heading fits more than one field and the app will not choose. |
| *not matched* | No field resembled it. Pick one, or leave it out. |

A column set to **— do not import —** is skipped. `Timestamp` and
`Email Address` are skipped automatically.

Tick **Remember this for next time** and your answers are written to
`config/form_mapping.json`, so the same form maps itself on every later import.
That file holds column headings and field ids only — never a client's answers —
which is why it is safe to keep in the repository.

## How a PDF is read

There are no columns in a PDF, so the app looks for the *questions* — the same
field labels it matches spreadsheet headings against — and takes the text
following each one as its answer, stopping at the next question or a blank line.

Two rules keep answers attached to the right question:

- The line under a question is its answer, even if it happens to read a bit like
  a question itself. "SAMPLE PETITIONER" contains the word *petitioner*, and
  without this rule it was mistaken for a heading and the real answer was lost.
- Only something that reads *almost exactly* like another question is treated as
  one instead — which is what a question the client skipped looks like.

Page numbers, form URLs and Google's "neither created nor endorsed" footer are
discarded.

Because this is inference rather than columns, **read the mapping screen
carefully the first time you import a given form**. Once you confirm it, the
wording is remembered and later imports of that form are exact.

## What it will not do

The importer never guesses when guessing could be wrong:

- **Two fields could fit** — left for you. Half a dozen fields are labelled
  "Date of birth"; a heading of just "State of birth" fits both the petitioner's
  and the child's, so it asks rather than picking one.
- **A value does not fit its field** — a date that is not a date, an option that
  is not on the list, an answer to a yes/no question that is neither — the field
  is left blank and you are told which column and why.
- **No field is filled from two columns.** Silently overwriting one answer with
  another is exactly the kind of quiet wrong this app exists to prevent.

Anything skipped is reported after the import, so nothing disappears without a
line saying so.

## Wording your form so it maps itself

The matcher reads the group word in a heading — "**Your** date of birth" is the
petitioner's, "**Child's** date of birth" is the child's. Write headings that
name who the question is about and nearly everything matches first time:

| Write this | Not this |
|---|---|
| Your date of birth | Date of birth |
| Child's state of birth | State of birth |
| Biological mother's full name | Mother |
| Second child's date of birth | Date of birth (2) |

The words each group answers to are in `DEFAULT_ALIASES` in
`app/responses.py`. To match your own form's vocabulary, give the group its own
list in `config/fields.json`:

```json
"petitioner1": { "label": "Petitioner", "order": 2,
                 "aliases": ["petitioner", "your", "adoptive parent"] }
```

Keep aliases specific. A word like "birth" appears inside "place of birth" and
"date of birth", and would drag every one of those columns towards the wrong
group.

None of this matters after the first import of a given form — the remembered
mapping takes over.

## Confidentiality

A completed form holds children's names and dates of birth, whether it arrived
as a spreadsheet or a PDF. `*.csv`, `*.tsv` and `*.pdf` are gitignored for that
reason. Keep the file with the matter, not in this repository.
