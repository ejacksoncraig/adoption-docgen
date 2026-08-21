# Adoption Filing Generator

Local desktop app that generates Oklahoma adoption court filings from a single
intake form. Replaces find-and-replace on `XXX` plus manual deletion of the
paragraphs that don't apply.

## Status

Scaffold. One template is tokenized and rendering (`dhs_petition_decree_1p_1c`).
No application code yet.

## Start here

Read `PROJECT_BRIEF.md`. Then `config/fields.json`, `config/matters.json`, and
`docs/TEMPLATE_AUTHORING.md`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Confidentiality

This repository must contain **blank templates only**. Generated documents,
saved intake files, and any document containing a real name, date of birth, or
case number are gitignored and must stay that way. Git history is permanent;
a client record committed once is committed forever.
