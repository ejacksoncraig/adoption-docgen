# PROJECT BRIEF — Adoption Filing Generator

Read this file completely before writing code. Then read `config/fields.json`,
`config/matters.json`, and inspect `templates/dhs/dhs_petition_decree_1p_1c.docx`
so you understand the template conventions already in use.

## What we are building

A local desktop application for a small Oklahoma law office (adoption practice).
Staff pick an adoption type, answer an intake form once, and the app generates
every required court document with names, dates, counties, and conditional
paragraphs already filled in.

Today this is done by opening a Word file, using Find & Replace on the string
`XXX`, and manually deleting the paragraphs that do not apply. That process is
slow and it produces filing errors.

## Non-negotiable constraints

1. **Fully local.** No server, no cloud, no network calls, no telemetry. All
   processing happens on the user's machine. The data is adoption records
   involving minors.
2. **Never write client data into the repo.** Generated documents go to an
   `output/` directory that is gitignored. Intake JSON goes to `intake/`, also
   gitignored. Do not add example files containing realistic names.
3. **Templates are data, not code.** Adding a new adoption type, a new county
   variant, or a new pleading must require only: dropping a `.docx` into
   `templates/`, and editing `config/matters.json` + `config/fields.json`.
   If a change like that forces a Python edit, the design is wrong.
4. **Fail loud, never silently.** A missing variable must stop generation with a
   clear message. A document that renders with a blank where a name should be is
   worse than a document that fails to render.

## Stack

- Python 3.11+
- `docxtpl` (Jinja2 templating for .docx) for the fill engine
- `pywebview` for the desktop window; the UI is plain HTML/CSS/JS inside it
- LibreOffice (`soffice --headless --convert-to pdf`) for optional PDF export,
  detected at runtime and degraded gracefully if absent
- `pyinstaller` for packaging to a single .exe

Do not use Electron. The fill engine is Python; adding a Node layer buys nothing.
Do not use Streamlit. We want a real window, not a browser tab.

## Repository layout

```
app/
  main.py           entry point, creates the pywebview window
  registry.py       loads + validates config/, exposes the matter catalog
  schema.py         field definitions, types, validation, derived fields
  engine.py         renders templates via docxtpl, exports PDF
  intake.py         save/load intake JSON, generate blank questionnaire
  ui/index.html     the entire front end (single file, no build step)
config/
  fields.json       every field the system knows about
  matters.json      adoption types -> variants -> template sets
templates/
  dhs/              DHS (state custody) templates
  stepparent/       step-parent templates
intake/             gitignored — saved intake JSON
output/             gitignored — generated documents
docs/
  TEMPLATE_AUTHORING.md   how to tokenize a new template
```

## Template conventions (already established — follow these exactly)

Templates are `.docx` files containing Jinja2 tags rendered by `docxtpl`.

- Simple substitution: `{{ petitioner1_name }}`
- Conditional inline text: `{% if child1_under_12 %}not {% endif %}required`
- Either/or paragraphs collapse into ONE paragraph with `{% if %}...{% else %}...{% endif %}`

**Critical:** the source documents use manually typed paragraph numbers ("11.",
"12.") as literal text, not Word auto-numbering. This means you must NOT use
docxtpl's paragraph-level tags (`{%p if %}`) to delete alternate paragraphs —
that leaves the numbers orphaned or renumbers nothing. Always collapse both
branches into a single paragraph using an inline `{% if %}/{% else %}`.

The pilot template `templates/dhs/dhs_petition_decree_1p_1c.docx` demonstrates
every one of these patterns. Use it as the reference.

## Core design: the registry

`config/matters.json` is the single source of expandability. Shape:

```json
{
  "matters": [
    {
      "id": "dhs",
      "label": "DHS / State Custody Adoption",
      "variants": [
        {
          "id": "dhs_1p_1c",
          "label": "1 petitioner, 1 child",
          "petitioners": 1,
          "children": 1,
          "documents": [
            { "template": "dhs/dhs_petition_decree_1p_1c.docx",
              "output_name": "Petition and Decree - {{ child1_name }}.docx" }
          ],
          "field_groups": ["case", "petitioner1", "child1", "bio_parents", "dhs", "icwa"]
        }
      ]
    }
  ]
}
```

`config/fields.json` defines each field once: id, label, type
(`text|date|select|bool|number`), which group it belongs to, whether it is
required, options for selects, and help text shown in the UI.

The UI is **generated from this config**. There are no hardcoded form fields in
`index.html`. Adding a field to `fields.json` and referencing it in a template is
all it takes for it to appear in the form.

## Startup validation (build this early — it is the safety net)

On launch, `registry.py` must:

1. Load every template referenced in `matters.json` and call
   `DocxTemplate.get_undeclared_template_variables()`.
2. Assert every variable it finds is either defined in `fields.json` or produced
   by `schema.py` as a derived field.
3. Assert every template file referenced actually exists on disk.
4. Report all problems at once in a readable list, then refuse to start.

This is what makes the system safe to expand. When someone adds a template with
`{{ petitoner1_name }}` misspelled, they find out immediately instead of filing a
petition with a blank name in it.

## Derived fields

`schema.py` computes these from entered values; they must never be typed twice:

- `county_upper` from `county`
- `child1_name_upper`, `petitioner1_name_upper` etc.
- `petitioner1_age` from date of birth and today's date
- `child1_birth_day` as an ordinal ("2nd"), plus `_month` and `_year`, all split
  from a single entered date
- `case_year` from today
- `child1_dob` formatted as M/D/YYYY

Enter a date once. The template decides how it is displayed.

## Application flow

1. **Select matter** — dropdown of adoption types from `matters.json`
2. **Select variant** — dropdown of variants (petitioner count, child count)
3. **Intake** — form rendered from the union of `field_groups` for that variant
4. **Import (optional)** — load a previously saved intake JSON, prefilling the form
5. **Review** — show which documents will be generated and any unanswered required fields
6. **Generate** — write .docx files to `output/<matter>_<child name>_<date>/`,
   optionally also PDF, then open that folder

## Intake handoff (the "adopter fills it out" path)

Do not build a hosted form. Instead:

- **Export questionnaire**: generate a plain `.docx` questionnaire from the field
  schema for the selected variant, which staff email to the adoptive parents.
  They fill it in, email it back, staff types it in. Low tech and legally safe.
- **Save/load intake JSON**: staff can save an in-progress intake and reopen it.

If a self-service client portal is ever wanted, that is a separate project with
its own security review. Do not build toward it now.

## Build order

Work in this sequence and confirm each step works before moving on.

1. `registry.py` + `schema.py` + startup validation, exercised from a CLI script.
2. `engine.py` rendering the one existing pilot template to `output/`, driven by a
   hardcoded dict. Verify the .docx opens in Word and reads correctly.
3. `intake.py` save/load JSON.
4. `ui/index.html` + `main.py`, wiring the pywebview bridge. Config-driven form.
5. PDF export via LibreOffice, with graceful degradation when it is missing.
6. Questionnaire generation.
7. PyInstaller packaging.

## Known gaps in the pilot template — do not treat these as done

These are hardcoded in the source document and still need tokenizing. Flag them
in `docs/TEMPLATE_AUTHORING.md` as the running TODO list:

- The verification block reads `COUNTY OF WAGONER` regardless of filing county
- The attorney signature block (name, OBA number, address, phone, email) is
  literal text in every document; it should become `{{ attorney_* }}` fields
  sourced from a settings file, not the intake form
- The notary date line has `2026` hardcoded
- Case number is `FA-2026-_____` with the year baked in
- Affidavit of Expenditures (a different template) has hourly rate, hours, filing
  fee, and totals hardcoded as text

## Testing

- A golden-file test: render the pilot template with a fixed fake context, extract
  text, compare against a committed expected-output text file. This catches
  template edits that silently break a conditional.
- A test asserting no rendered output contains the literal strings `XXX`, `{{`, or
  `{%`. Leftover placeholders in a filed document are the failure mode that
  matters most.
- Test both branches of every conditional (ICWA on/off, name change on/off,
  relinquished vs terminated, under/over 12).

## Out of scope

E-filing integration, calendaring, billing, document assembly for anything other
than the templates in `templates/`. Do not add dependencies beyond those listed
without asking.
