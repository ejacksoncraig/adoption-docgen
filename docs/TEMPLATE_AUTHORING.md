# Template Authoring

How to turn one of the office's Word documents into a template this app can fill.

## The rule that matters most

The source documents use **manually typed paragraph numbers** — the "11." in
paragraph 11 is literal text, not Word auto-numbering.

So when a document offers two versions of a paragraph separated by "Or", do
**not** delete one paragraph with a `{%p if %}` tag. Deleting the paragraph leaves
the numbering wrong. Instead, collapse both versions into the **first** paragraph
using an inline conditional, and blank out the text of the alternate paragraph and
its "Or" separator.

Wrong:

```
{%p if mother_relinquished %}
11.  That the biological mother ... relinquishment ...
{%p else %}
11.  That the biological mother ... terminated ...
{%p endif %}
```

Right — one paragraph, one number, branch inside it:

```
11.  {% if bio_mother_status == "relinquished" %}That the biological mother of
the child is {{ bio_mother_name }} and that the parent executed ... relinquishment
...{% else %}That the biological mother of the child is {{ bio_mother_name }} and
the parent's consent is not required because ... terminated ...{% endif %}
```

## Naming convention

`{{ entity }}{{ index }}_{{ attribute }}` — lowercase, underscores.

- `petitioner1_name`, `petitioner2_address`
- `child1_birth_county`, `child2_dob`
- `bio_mother_status`, `bio_father_name`

Same concept, same name, in every template. This is what keeps the fill engine
ten lines long instead of a per-template mapping table.

Never invent a name at template-authoring time. Add it to `config/fields.json`
first, then use it. Startup validation will reject anything not declared there.

## Enter a value once

Do not create `child1_birth_day`, `child1_birth_month`, and `child1_birth_year`
as things a human types. A human types `child1_dob` as a date; `app/schema.py`
splits it and computes the ordinal ("2nd"). Same for age, uppercase variants, and
the under-12 flag.

If a template needs a value in a new shape, add a derived field. Don't add another
box to the form.

### The derivations you can ask for

Mark the field `"derived": true` in `fields.json` and name it to match one of
these patterns. Nothing in Python needs to change — the rule already exists and
works for any entity that follows the naming convention.

| Name it | You get | It reads |
|---|---|---|
| `<anything>_upper` | uppercased | the field of that name without `_upper` |
| `<entity>_birth_day` | `2nd` | `<entity>_dob` |
| `<entity>_birth_month` | `March` | `<entity>_dob` |
| `<entity>_birth_year` | `2015` | `<entity>_dob` |
| `<entity>_age` | `41` | `<entity>_dob` and today's date |
| `<entity>_under_<n>` | `true` / `false` | `<entity>_dob` and today's date |
| `case_year` | `2026` | today's date |
| `<entity>_pronoun` | `he` / `she` | `<entity>_gender` or `<entity>_relationship` |
| `<entity>_pronoun_object` | `him` / `her` | same |
| `<entity>_pronoun_possessive` | `his` / `her` | same |
| `attorney_<anything>` | the office's own details | `config/settings.json` |

Pronouns read whichever source field exists, so `male`/`female` and
`father`/`mother` both work. Pick the right one for the sentence — this is the
easiest thing in the whole system to get wrong, because every version of it is
grammatical:

```
{{ other_parent_pronoun }} consents            -> "he consents"          correct
{{ other_parent_pronoun }} consent is not       -> "he consent is not"    wrong
{{ other_parent_pronoun_possessive }} consent   -> "his consent"          correct
{{ other_parent_pronoun }}'s parental rights    -> "he's parental rights" wrong
{{ other_parent_pronoun_possessive }} rights    -> "his parental rights"  correct
```

A value the pronoun table does not recognise stops generation rather than
guessing. A pleading that calls a mother "he" is one a judge will notice.

So `petitioner2_name_upper` or `child2_under_18` work the moment you declare
them. A derived field whose source field does not exist, or whose name matches no
rule, is a startup error — the app will say so and refuse to launch.

Anything else — a new *kind* of computation — is a new rule in the
`DERIVATIONS` table in `app/schema.py`. Adding one is the only kind of Python
change template work should ever require.

### Showing one date in several shapes

A date field is typed once and displayed however each sentence needs it. Set
`"format"` on the field:

| format | prints | use it for |
|---|---|---|
| `mdy` (default) | `3/2/2015` | the `DOB:` line in a caption |
| `long` | `March 2, 2015` | ordinary prose |
| `long_ordinal` | `2nd day of March, 2015` | "on the ____," phrasing |
| `iso` | `2015-03-02` | never in a filing; file names |

`foster_placement_date` uses `long_ordinal` because ¶6 reads "obtained foster
parent placement of the minor child on the {{ foster_placement_date }}".

### Values with a fallback

`"default"` is a small Jinja string used when the field is left blank, evaluated
after derived fields are computed. `case_number` is
`"FA-{{ case_year }}-_____"`, so leaving it blank prints the year and a blank for
the clerk instead of a hardcoded `FA-2026-_____`.

### Fields the family should not be asked

A group marked `"questionnaire": false` in `fields.json` is left off the .docx
questionnaire emailed to adoptive parents. `case` is marked that way: the filing
county and case number are the office's to fill in.

A single field can opt out the same way, when the rest of its group does belong
on the questionnaire. `attorney_fees_summary` sits in the `dhs` group — the
family answers the rest of that group, but not the fee schedule.

## The caption is a table

Every caption in every template — 32 of them, since a packet holds several
documents — is a borderless 1x3 table:

| party text | ) | case number |
|---|---|---|

Column one holds the lines as they read, column two holds one `)` per line,
column three holds the case number. The table declares `w:tblBorders` all `none`
and zeroes `w:tblCellMar`, so on the page it is the caption it always was.

It used to be drawn with tab stops: each line tabbed out to its `)` and, on one
line, on again to the case number. That lines up only while the text to the left
stays short. A child's name long enough to reach the stop pushes its own `)` onto
the next one, and that single parenthesis steps out of the column — on the first
page of a filing, in the part nobody re-reads. A column cannot drift.

Only the `A Minor Child.` / `Minor Children.` line is indented, half an inch;
nothing else in the caption is. `tests/test_all_variants.py` pins all of it:
the shape (`test_every_caption_is_a_one_row_three_column_table`, matched on one
row by three columns, which is what tells a caption apart from the notice's
receipt grid), the indent, and that no table anywhere prints a rule.

A caption that branches — the notice prints five lines for one child and seven
for two — carries its `{%p if %}` in **both** the text column and the
parentheses column, so the two stay the same height. The case-number column
needs no branch: the number falls on the same line in either arm, so that column
is built from the first arm alone. Building it from every arm prints the case
number once per branch, which is the mistake to avoid if a third arm is ever
added.

### No table anywhere prints a rule

Every table in these templates is a layout device — a caption, an address block,
a receipt grid — and a printed border on any of them is a box drawn across a
court filing. Five templates had visible rules left over from the `.doc`
conversion; they are off now and
`test_no_table_in_any_template_prints_a_rule` keeps them off. That test also
fails a table that declares no borders at all, since the default is to show them.

## Where a numbered allegation sits on the page

Every numbered paragraph, in every template, indents its first line half an inch.
The number sits at 0.5" and one tab carries its text to the next stop:

```
    1.<tab>That the Petitioner has been a resident of Wagoner County ...
```

`w:ind w:firstLine="720"`, and nothing else. Converting the office's `.doc`
originals had left them all at 1", which put the number an inch in and its text
half an inch further again. The originals were not consistent either — the
notice ran two levels, its numbered rights sitting a further half-inch in than
the allegations above them — so the office was asked and chose the single rule.
127 paragraphs across the ten templates.

Pinned by `test_every_numbered_paragraph_is_indented_half_an_inch` in
`tests/test_all_variants.py`, which reads the templates themselves rather than a
render. Nothing else in the suite would catch this drifting back: a re-converted
template would return to 1" and every *value* in it would still be correct.

When adding a template, note that a `w:ind` has a place in the schema's running
order for `w:pPr` — after `w:spacing`, before `w:jc`. Word tolerates it appended
anywhere; stricter readers do not.

A numbered paragraph must be pushed across by its indent **alone**: `1.` then a
tab then the text. The office's originals also contain two hand-made variants —
tabs typed in front of the number, and a space after it instead of a tab — and
both add to the indent rather than replacing it, so the allegation hangs
differently from the ones above it. Five such paragraphs survived the first two
passes over the numbering because the test only matched `1.<tab>` at the very
start of a paragraph. `test_a_numbered_paragraph_is_pushed_across_by_its_indent_alone`
now catches them.

Prose paragraphs — "Comes now …", "WHEREFORE, premises considered …" — still
carry the converted 1" first-line indent, and nobody has asked about them. The
notice's original uses 0.5" for its prose, so they are probably a half-inch out
too. If that gets fixed, fix it the same way: read the original, do not guess.

## One document filed in several variants

The DHS consent is filed in every DHS adoption, but it names a second petitioner
only when there is one. Rather than keeping two near-identical templates, give
the document the extra groups it *may* use:

```json
{ "template": "dhs/dhs_consent_affidavit_order.docx",
  "output_name": "Consent, Affidavit of Expenditures, Order for Hearing.docx",
  "field_groups": ["case", "petitioner1", "petitioner2", "child1"] }
```

and guard those values in the template:

```
... by {{ petitioner1_name }}{% if petitioner2_name %} and {{ petitioner2_name }}{% endif %}.
```

`field_groups` on a *document* means "these groups on top of whatever the variant
collects". Anything from an extra group may be absent at generation time, so:

- `{% if petitioner2_name %}` — allowed, and false when there is no second petitioner.
- `{{ petitioner2_name }}` outside that guard — still fails, still writes nothing.

That is `GuardedUndefined` in `app/registry.py`. Asking whether a value is there
is a fair question; printing one that is not is a blank where a name belongs.

Use this only for values that are genuinely optional in that document. If a
template needs a value in every case, add the group to the variant's own
`field_groups` instead, so the form asks for it.

## Three counties, and none of them interchangeable

The juvenile documents are the first templates that cannot just print
`{{ county }}`. Three different courts are in play:

| Field | The court it names |
|---|---|
| `county` | where the adoption is filed — the heading of every other template |
| `juvenile_county` | where the deprived case sits, the heading of both juvenile documents |
| `probate_county` | the court being asked to take the adoption, named in the body of the concurrent jurisdiction application and order |

Asking for concurrent jurisdiction only makes sense when the first two differ,
so `juvenile_county` is the one that will actually be filled in. Both extra
fields default to `{{ county | default('', true) }}` — the usual case is that
they are all the same and nobody should answer the same question three times to
say so.

Note the `| default('', true)`. A default is rendered *after* derived values and
against whatever is in the context, so a default that reads another answer has
to survive that answer being absent — which it is on an empty draft. Without the
filter, drafting a blank intake raises `'county' is undefined` instead of
marking the gap. The same trap caught the affidavit's fee sentence.

Because the heading needs the county in capitals and a defaulted field cannot be
the source of a derived `_upper` (the derivation runs first, the default second),
these templates use Jinja's own filter: `{{ juvenile_county|upper }}`. That is
the exception to the `_upper` convention, and the reason for it.

## A document that is not always filed

Most documents in a filing are guarded *inside* the template — ICWA off prints
a different paragraph, but the document itself is still generated. The Notice
to Tribe is different: when ICWA does not apply there is nothing for it to
say, so it should not be generated at all rather than rendered with blank or
inapplicable content.

A document entry in `matters.json` can name a bool field it depends on:

```json
{ "template": "noticetotribes/notice_to_tribe.docx",
  "output_name": "4 Notice to Tribe - {{ tribe }} - {{ child1_name }}.docx",
  "status": "ready",
  "depends_on": "icwa_applies",
  "field_groups": ["petitioner2", "child2", "stepparent"] }
```

`app/engine.py::generate()` (and `Api.review` in `app/main.py`, so the preview
screen agrees with what actually gets written) drop the document from the plan
entirely when that field is falsy in the intake values — it never appears in
the output folder and is never asked to guard its own variables. Use this only
when a document has nothing to say when the condition is off; if it merely
reads differently, guard the paragraph with `{% if %}` instead and keep
generating the document.

## Adding another tribe to the Notice to Tribe

`templates/noticetotribes/notice_to_tribe.docx` prints the correct tribal ICW
office address automatically, matched against the `tribe` field's exact text.
Five tribes are wired up: Cherokee Nation, Chickasaw Nation, Choctaw Nation of
Oklahoma, Muscogee (Creek) Nation, and Citizen Potawatomi Nation. A `tribe`
value that matches none of them still produces a notice — the office name
prints as typed, and the address block prints a bracketed reminder to fill it
in by hand rather than guessing.

To wire up another tribe, add one more `{% elif tribe == "..." %}` branch to
each of the three address lines in the template (department, street/PO box,
city/state/ZIP) — the tribe name itself already prints from `{{ tribe }}`, so
it does not need its own branch. All three lines must test the exact same
string. There is no Python change involved.

### How this template was authored

Unlike the others, `notice_to_tribe.docx` was not tokenized from the office's
original Word file. It was rebuilt from a *generated* notice that the office had
edited in Word until the page looked right — line spacing, the caption's column
of parentheses, which headings are bold and which are underlined, and two layout
tables in the certificate of service. Replaying that many formatting decisions
onto the old template would have been guesswork, so the edited document became
the new base and the tokens were put back into it.

Do the same thing again if the layout needs another pass: generate a notice,
edit it in Word until it is right, and re-tokenize. Trying to hand-patch the
paragraph properties is how the caption's tab counts get broken.

Two things about that document differ from what the office typed, on purpose:

- The certificate's first address block had lost its department line ("ICW
  Adoptions") when it was retyped into a table, while the other two blocks kept
  it. All three now print the same four lines from the same conditionals.
- Paragraph 2 read "The children are an \"Indian Child\"" in the plural. It now
  reads "are each an \"Indian Child\"".

### What the notice asks for that nothing else does

Three fields in the `icwa` group exist only for this document, and all three are
optional, because a notice is usually drafted before the hearing is set:

| Field | Fills |
|---|---|
| `petition_hearing_date` | ¶1, "will be heard on the 18th day of June, 2026" |
| `courthouse_city` | ¶1, "in the District Courthouse at Newkirk, Kay County" |
| `judge_name` | ¶1, "before the Honorable Judge …" |

Each has a `default` of the underscores the office's own form prints, so leaving
one blank gives back the line to fill in by hand rather than a gap. `judge_name`
is the one that is often left blank on purpose; the other two are worth asking
for. The date the notice is posted is *not* asked for: it was, briefly, and the
office asked for blanks back, because a date printed there would be the date the
filing was prepared rather than the date it was mailed, and the certificate
swears to the second of those. `courthouse_city` is asked rather than derived from `county` because a
county seat is not the county's name — Kay County's courthouse is in Newkirk.

All three are marked `"questionnaire": false`. They are the office's to answer,
not the adoptive family's.

## The office's own details

The attorney signature block does not belong on the intake form — it is the same
on every filing. Put it in `config/settings.json` and reference it from templates
as `{{ attorney_short_name }}`, `{{ attorney_oba }}` and so on, declaring each one
in `fields.json` as a derived field in the `case` group.

`config/settings.json` ships empty. A template that needs a value it does not have
refuses to generate and names the missing setting. That is deliberate: a decree
that names no attorney of record is worse than one that fails to render.

## The attorney's signature

The office uploads one signature image under **Office details**. It is stored
beside `config/settings.json` as `config/signature.<ext>`, belongs to the
installation rather than the repository, is gitignored, and survives a rebuild.

Templates reach it as `{{ attorney_signature }}`, guarded:

```
By{% if attorney_signature %}{{ attorney_signature }}{% else %}_________________________{% endif %}
```

```
By:{% if attorney_signature %}  {{ attorney_signature }}{% endif %}
```

The guard is not optional. With no image on file — or with signing switched off
for a filing on the Review screen — the token is absent and the line has to print
as it always did. `attorney_signature` is declared `"optional": true` in
`fields.json`, which is what stops the pre-render check from treating a missing
signature as an unfilled setting; see `FieldDef.optional` in `app/schema.py`.

`{{ attorney_signature }}` reaches the context as a *path*, because that is what
a derived `attorney_` field can carry. `app/engine.py::with_signature` swaps it
for a docxtpl `InlineImage` at render time — an InlineImage writes the picture
into one document's package, so it cannot be built once and shared across the
four documents of a filing.

### Put it only where the attorney signs

There are 16 signature spots across the ten templates, and every one of them sits
directly above the block naming the attorney. The petitioners' lines, the
notary's, the judge's, and the attorney blocks printed under a judge's signature
as "prepared by" are all deliberately left alone.

`test_every_signature_sits_directly_above_the_attorneys_own_block` in
`tests/test_signature.py` enforces exactly that: it finds every drawing in a
generated filing and requires the next non-empty line to name the attorney. If a
new template puts `{{ attorney_signature }}` anywhere else, that test fails —
which is the point. This program signs on the attorney's behalf, so where it will
and will not do so needs to be a rule rather than a convention.

The size is fixed in `app/signature.py`: scaled to fit 12 mm tall by 60 mm wide,
aspect kept. Height alone is not enough — a signature scanned as a long thin
strip would print four inches wide and run off the end of its line.

## Procedure for a new template

1. Copy the `.doc` to `templates/_source/` (gitignored) and convert to `.docx`.
2. **Scrub it.** Search for any real client name, DOB, or case number left over
   from a prior filing and replace it with a token. Do this before the file goes
   anywhere near git.
3. Search for `XXX`. Each instance is a different field. Work through them one at
   a time and replace each with the correct named token.
4. Search for `Or`, `OR`, and square brackets like `[not]` or `[less than]`. Each
   one is a branch. Collapse it per the rule above.
5. Search for any county, date, dollar amount, or attorney detail typed as literal
   text. Those are the ones that quietly produce a wrong filing.
6. Save into `templates/<matter>/`, register it in `config/matters.json`, and set
   `"status": "ready"`.
7. Run `python -m app.cli check`. Startup validation reports every undeclared
   variable, missing template and out-of-group field at once. The app runs the
   same check at launch and refuses to start if it fails.
8. Render both sides of every conditional and read the output:

   ```bash
   python -m app.cli render <matter> <variant>            # every branch one way
   python -m app.cli render <matter> <variant> --falsy    # and the other
   ```

   Both write to `output/` with obvious placeholder answers. Open them in Word
   and read them. The tests check strings; only a person can check that a
   paragraph still makes sense.
9. Add the template to the tests. At minimum, one assertion per branch — see
   `tests/test_render.py`. If it is a pleading the office files often, give it a
   golden file too (`tests/test_golden.py`).

## Reference implementation

`templates/dhs/dhs_petition_decree_1p_1c.docx` is fully tokenized and demonstrates
every pattern: simple substitution, inline word-level conditionals
(`{% if child1_under_12 %}not {% endif %}required`), whole-paragraph either/or
collapse (biological parent status, ICWA), and optional-clause suppression (name
change). Read it before authoring a new one.

---

## Running TODO — hardcoded values still in the source documents

These are baked into the original Word files and will silently produce wrong
filings until they are tokenized. Tick them off as they are fixed.

Done in `dhs_petition_decree_1p_1c.docx`:

- [x] Verification block read `COUNTY OF WAGONER` regardless of filing county.
      Now `COUNTY OF {{ county_upper }}`; covered by
      `test_every_county_in_the_document_follows_the_filing_county`.
      **Still to do in both step-parent petitions when they are tokenized.**
- [x] Notary line had `2026` hardcoded. Now `{{ case_year }}`, which is the year
      the document is generated.
- [x] Caption case number had the year baked in. The caption uses
      `{{ case_number }}`, and leaving the field blank prints
      `FA-<current year>-_____` via the `default` in `fields.json`.

Outstanding:

- [ ] Attorney signature block — name, OBA #, address, phone, email — is literal
      text in every document. The machinery is ready: fill in
      `config/settings.json`, declare each value in `fields.json` as a derived
      field in the `case` group, and replace the literal lines with
      `{{ attorney_full_name }}`, `{{ attorney_oba }}` and so on. Until then the
      block prints one fixed address, which is wrong the day the office moves or
      a second attorney signs. Note `{{ attorney_short_name }}` is *already* used
      in the decree, so `settings.json` must be filled in before any real filing.
- [ ] Affidavit of Expenditures has hourly rate ($300), hours (12.00), filing fee
      ($184.14), amended birth certificate fee ($40) and total ($3,824.14) as
      literal text. Filing fees vary by county and change over time. The total is
      the dangerous one — it must be derived from the parts, not typed, or the
      three will drift apart. This was built once, computing the total from four
      separate answers, and the office preferred the single box they could write
      in freehand; the computed version is in the history at commit 3bdb556 if it
      is ever wanted back.
- [ ] `0all_inclusive_Step_parent.doc` contains two real minors' names and DOBs
      and a real county from a prior case. **Scrub before this file is committed
      anywhere.**
- [ ] `Petition_for_Adoption_step_parent_1_child.doc` has Wagoner hardcoded in the
      caption where sibling templates use a placeholder
- [x] DHS petition ¶6 had a foster placement date of "21st day of September,
      1999" left over from an old matter. Now `{{ foster_placement_date }}`,
      formatted `long_ordinal`.
- [x] Step-parent petition ¶13 (failure to maintain a relationship) used `XXX`
      for the parent role, name, and pronouns. Now driven by
      `other_parent_relationship`, `other_parent_name` and the pronoun
      derivations, across all three consent bases. Covered by
      `tests/test_stepparent.py`.

## Templates still to write

All templates named in `config/matters.json` now exist and all six variants are
`"status": "ready"`. `python -m app.cli check` lists anything still pending.
Nothing else needs changing when a new one is finished — drop the `.docx` into
`templates/<matter>/`, register it, and set `"status": "ready"`.

Note the folder matters: `matters.json` referring to `stepparent/step_packet.docx`
means the file must be in `templates/stepparent/`, not `templates/dhs/`. One
template can be shared by more than one matter — `noticetotribes/notice_to_tribe.docx`
is a `common_document` of both `dhs` and `stepparent`. Where the two matters hold
the same fact under different names, it names both groups as optional and asks
for each in turn: ¶3 wants the birth parents, which DHS keeps in `bio_parents`
and a step-parent matter splits between petitioner 1 and `stepparent`. So the
DHS entry lists `stepparent` as an extra group and the step-parent entry lists
`bio_parents`, and the paragraph guards both.

## Coverage as it stands

`dhs_petition_decree_1p_1c` has a golden file and per-branch assertions. The
step-parent petitions have per-branch assertions for all three consent bases and
both parents. `notice_to_tribe` has per-branch assertions in
`tests/test_notice_to_tribe.py`: generated only when ICWA applies, for either
matter, the correct address for each of the five known tribes plus the
fallback for one that is not, singular/plural for one child versus two, ¶1 and
the certificate of service both blank and filled in, the birth parents named
from `bio_parents` in a DHS filing and from the petitioner plus `stepparent` in
a step-parent one, the signature blocks at their 3" and 3.25" indents, the
caption's tab counts for one child and for two, and all three address blocks
printing the same four lines.
The remaining templates — `dhs_petition_decree_2p_1c`,
`dhs_petition_1p_2c`, `dhs_petition_2p_2c`, `dhs_packet`, `step_packet` — are
only covered by the smoke check that they render at all with no placeholder left
behind. Add branch assertions to those before they are relied on for a real
filing.
