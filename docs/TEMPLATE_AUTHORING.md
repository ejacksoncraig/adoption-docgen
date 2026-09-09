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
  "field_groups": ["petitioner2", "child2"] }
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

## The office's own details

The attorney signature block does not belong on the intake form — it is the same
on every filing. Put it in `config/settings.json` and reference it from templates
as `{{ attorney_short_name }}`, `{{ attorney_oba }}` and so on, declaring each one
in `fields.json` as a derived field in the `case` group.

`config/settings.json` ships empty. A template that needs a value it does not have
refuses to generate and names the missing setting. That is deliberate: a decree
that names no attorney of record is worse than one that fails to render.

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
      three will drift apart.
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
is a `common_document` of both `dhs` and `stepparent` — as long as it only
reaches for fields both matters' variants actually collect (it does not use
`bio_parents`, which only DHS variants have).

## Coverage as it stands

`dhs_petition_decree_1p_1c` has a golden file and per-branch assertions. The
step-parent petitions have per-branch assertions for all three consent bases and
both parents. `notice_to_tribe` has per-branch assertions in
`tests/test_notice_to_tribe.py`: generated only when ICWA applies, for either
matter, the correct address for each of the five known tribes plus the
fallback for one that is not, and singular/plural for one child versus two.
The remaining templates — `dhs_petition_decree_2p_1c`,
`dhs_petition_1p_2c`, `dhs_petition_2p_2c`, `dhs_packet`, `step_packet` — are
only covered by the smoke check that they render at all with no placeholder left
behind. Add branch assertions to those before they are relied on for a real
filing.
