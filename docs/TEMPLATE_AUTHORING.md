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
7. Run the app. Startup validation will tell you about any undeclared variable.
8. Render both sides of every conditional and read the output.

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

- [ ] Verification block reads `COUNTY OF WAGONER` regardless of filing county
      (appears in the DHS petition and both step-parent petitions)
- [ ] Attorney signature block — name, OBA #, address, phone, email — is literal
      text in every document. Should be `{{ attorney_* }}` from a settings file,
      not from intake.
- [ ] Notary line has `2026` hardcoded
- [ ] Caption case number is `FA-2026-_____`, year baked in
- [ ] Affidavit of Expenditures has hourly rate ($300), hours (12.00), filing fee
      ($184.14), amended birth certificate fee ($40) and total ($3,824.14) as
      literal text. Filing fees vary by county and change over time.
- [ ] `0all_inclusive_Step_parent.doc` contains two real minors' names and DOBs
      and a real county from a prior case. **Scrub before this file is committed
      anywhere.**
- [ ] `Petition_for_Adoption_step_parent_1_child.doc` has Wagoner hardcoded in the
      caption where sibling templates use a placeholder
- [ ] DHS petition ¶6 contains a foster placement date of "21st day of September,
      1999" left over from an old matter
- [ ] Step-parent petition ¶13 (failure to maintain a relationship) uses `XXX` for
      the parent role, name, and pronouns; needs gendered derived fields
