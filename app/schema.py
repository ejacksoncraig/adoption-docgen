"""Field definitions, intake validation, and derived values.

Everything here is driven by ``config/fields.json``. Adding a *field* is a config
edit. The only reason to touch this module is to teach it a new *kind* of
derivation — a new suffix rule such as ``_upper`` or ``_age`` — which then works
for every entity that follows the naming convention.

Two error types, deliberately distinct:

``ConfigError``  the repository is wrong (bad JSON, undeclared variable, a
                 derived field nothing knows how to compute). Caught at startup,
                 refuses to launch. A developer fixes it.
``IntakeError``  the entered data is wrong (missing required answer, bad date).
                 Carries a list of problems for the UI to display. A user fixes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable, Sequence

# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class ConfigError(Exception):
    """The configuration or a template is wrong. Reported at startup."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


class IntakeError(Exception):
    """The entered intake data is unusable. Reported to the user, not fatal."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


# --------------------------------------------------------------------------
# formatting primitives
# --------------------------------------------------------------------------

MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', 22 -> '22nd'."""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{_ORDINAL_SUFFIX.get(n % 10, 'th')}"


DATE_STYLES = ("mdy", "long", "long_ordinal", "month_year", "iso")


def format_date(d: date, style: str) -> str:
    """Render a date the way a pleading wants to read it.

    mdy           3/9/2024          (caption "DOB:" lines)
    long          March 9, 2024
    long_ordinal  9th day of March, 2024   (body prose: "on the ____,")
    month_year    March of 2024     (body prose: "in ____,")
    iso           2024-03-09        (filenames, saved intake)

    month_year exists because a placement is remembered as a month, not a day.
    Asking for an exact date and then printing it produced "obtained placement
    of the minor children on the 1st day of December, 2024" — a precision the
    office does not have and the court does not want.
    """
    if style == "mdy":
        return f"{d.month}/{d.day}/{d.year}"
    if style == "long":
        return f"{MONTH_NAMES[d.month]} {d.day}, {d.year}"
    if style == "long_ordinal":
        return f"{ordinal(d.day)} day of {MONTH_NAMES[d.month]}, {d.year}"
    if style == "month_year":
        return f"{MONTH_NAMES[d.month]} of {d.year}"
    if style == "iso":
        return d.isoformat()
    raise ConfigError([f"unknown date format {style!r} (expected one of {', '.join(DATE_STYLES)})"])


def parse_date(value: Any) -> date:
    """Accept an ISO date (what the UI sends) or a real date object."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{value!r} is not a date the app understands (use YYYY-MM-DD)")


def years_between(born: date, on: date) -> int:
    """Whole years elapsed — birthday-aware, the way an age is stated in a filing."""
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "yes", "y", "1", "on"}


def is_blank(value: Any) -> bool:
    """Bools and numbers are never blank; False is a real answer."""
    if isinstance(value, bool):
        return False
    if value is None:
        return True
    return str(value).strip() == ""


# --------------------------------------------------------------------------
# field definitions
# --------------------------------------------------------------------------

FIELD_TYPES = ("text", "date", "select", "bool", "number")


@dataclass(frozen=True)
class FieldDef:
    id: str
    group: str
    type: str
    label: str
    required: bool = False
    derived: bool = False
    options: tuple[str, ...] = ()
    help: str = ""
    depends_on: str | None = None
    date_format: str = "mdy"
    default: str | None = None
    multiline: bool = False
    #: A select field with a long option list (Oklahoma's 77 counties) is faster
    #: to reach by typing than by scrolling. Purely a UI hint for form_spec —
    #: validation still checks the answer against options the same as any
    #: other select, so free-typed text that matches nothing is caught at
    #: Review exactly like a mistyped answer to any other field.
    searchable: bool = False
    #: None means "follow the group". False keeps one field off the questionnaire
    #: even though the rest of its group belongs there.
    questionnaire: bool | None = None
    #: A derived value that may legitimately have nothing behind it. Every other
    #: derived field missing at generation time is a fault worth refusing over —
    #: an attorney_ field means settings.json was never filled in. The attorney's
    #: signature image is the exception: not having one is an ordinary state of
    #: the office, so the template guards it and the absence is not reported as
    #: a gap or an error. Templates must still guard it; printing an optional
    #: field outside its guard fails the same as any other (GuardedUndefined).
    optional: bool = False

    @classmethod
    def from_json(cls, raw: dict, problems: list[str]) -> "FieldDef | None":
        fid = raw.get("id")
        if not fid:
            problems.append(f"fields.json: an entry has no 'id': {raw!r}")
            return None
        ftype = raw.get("type", "text")
        if ftype not in FIELD_TYPES:
            problems.append(f"fields.json: {fid} has type {ftype!r} (expected one of {', '.join(FIELD_TYPES)})")
        if not raw.get("group"):
            problems.append(f"fields.json: {fid} has no 'group'")
        date_format = raw.get("format", "mdy")
        if ftype == "date" and date_format not in DATE_STYLES:
            problems.append(f"fields.json: {fid} has format {date_format!r} (expected one of {', '.join(DATE_STYLES)})")
        derived = bool(raw.get("derived", False))
        if derived and raw.get("required"):
            problems.append(f"fields.json: {fid} is derived and cannot also be required")
        return cls(
            id=fid,
            group=raw.get("group", ""),
            type=ftype,
            label=raw.get("label") or fid,
            required=bool(raw.get("required", False)),
            derived=derived,
            options=tuple(raw.get("options", ())),
            help=raw.get("help", ""),
            depends_on=raw.get("depends_on"),
            date_format=date_format,
            default=raw.get("default"),
            multiline=bool(raw.get("multiline", False)),
            questionnaire=raw.get("questionnaire"),
            searchable=bool(raw.get("searchable", False)),
            optional=bool(raw.get("optional", False)),
        )


# --------------------------------------------------------------------------
# derivation rules
# --------------------------------------------------------------------------
#
# A derived field is matched by pattern against these rules, in order. Each rule
# names the field it reads from, so a derived field whose source is missing from
# config is a startup error rather than a blank in a filed document.


@dataclass(frozen=True)
class Derivation:
    name: str
    pattern: re.Pattern
    #: field(s) this reads. None when it needs no input; a tuple when more than
    #: one field could supply the answer — the first one present is used.
    source: Callable[[re.Match], str | tuple[str, ...] | None]
    compute: Callable[[re.Match, dict[str, Any], date], Any]

    def sources(self, m: re.Match) -> tuple[str, ...]:
        found = self.source(m)
        if found is None:
            return ()
        return (found,) if isinstance(found, str) else tuple(found)


def _dob_of(m: re.Match) -> str:
    return f"{m.group('entity')}_dob"


def _first_present(candidates: tuple[str, ...], ctx: dict[str, Any]) -> Any:
    for name in candidates:
        if name in ctx:
            return ctx[name]
    return None


DERIVATIONS: tuple[Derivation, ...] = (
    Derivation(
        name="uppercase",
        pattern=re.compile(r"^(?P<base>.+)_upper$"),
        source=lambda m: m.group("base"),
        compute=lambda m, ctx, today: str(ctx[m.group("base")]).upper(),
    ),
    Derivation(
        name="birth date part",
        pattern=re.compile(r"^(?P<entity>.+?)_birth_(?P<part>day|month|year)$"),
        source=_dob_of,
        compute=lambda m, ctx, today: _birth_part(ctx[_dob_of(m)], m.group("part")),
    ),
    Derivation(
        name="age in years",
        pattern=re.compile(r"^(?P<entity>.+?)_age$"),
        source=_dob_of,
        compute=lambda m, ctx, today: years_between(parse_date(ctx[_dob_of(m)]), today),
    ),
    Derivation(
        name="age threshold flag",
        pattern=re.compile(r"^(?P<entity>.+?)_under_(?P<years>\d+)$"),
        source=_dob_of,
        compute=lambda m, ctx, today: years_between(parse_date(ctx[_dob_of(m)]), today) < int(m.group("years")),
    ),
    Derivation(
        name="pronoun",
        pattern=re.compile(r"^(?P<entity>.+?)_pronoun(?:_(?P<form>object|possessive))?$"),
        source=lambda m: (f"{m.group('entity')}_gender", f"{m.group('entity')}_relationship"),
        compute=lambda m, ctx, today: _pronoun(
            _first_present((f"{m.group('entity')}_gender", f"{m.group('entity')}_relationship"), ctx),
            m.group("form"),
        ),
    ),
    Derivation(
        name="current year",
        pattern=re.compile(r"^case_year$"),
        source=lambda m: None,
        compute=lambda m, ctx, today: str(today.year),
    ),
    Derivation(
        name="office settings",
        pattern=re.compile(r"^attorney_.+$"),
        source=lambda m: None,
        compute=lambda m, ctx, today: _from_settings(m.string, ctx),
    ),
)


#: What a gender or a parental role implies about pronouns. Keyed on the values a
#: select field can hold, so "male" and "father" both land on the same row.
_PRONOUNS: dict[str, tuple[str, str, str]] = {
    #                subject, object, possessive
    "male":     ("he", "him", "his"),
    "father":   ("he", "him", "his"),
    "man":      ("he", "him", "his"),
    "female":   ("she", "her", "her"),
    "mother":   ("she", "her", "her"),
    "woman":    ("she", "her", "her"),
}

_PRONOUN_FORMS = {None: 0, "object": 1, "possessive": 2}


def _pronoun(value: Any, form: str | None) -> str:
    """he / him / his, or she / her / her, from a gender or a parental role.

    Anything the table does not recognise stops generation rather than guessing:
    a pleading that calls a mother "he" is one a judge will notice.
    """
    key = str(value).strip().lower()
    if key not in _PRONOUNS:
        raise IntakeError(
            [f"{value!r} is not a value this system knows a pronoun for "
             f"(expected one of {', '.join(sorted(_PRONOUNS))})"]
        )
    return _PRONOUNS[key][_PRONOUN_FORMS[form]]


def _birth_part(raw: Any, part: str) -> str:
    d = parse_date(raw)
    if part == "day":
        return ordinal(d.day)
    if part == "month":
        return MONTH_NAMES[d.month]
    return str(d.year)


_SETTINGS_KEY = "__settings__"


def _from_settings(field_id: str, ctx: dict[str, Any]) -> Any:
    return ctx.get(_SETTINGS_KEY, {}).get(field_id, "")


def match_derivation(field_id: str) -> tuple[Derivation, re.Match] | None:
    for rule in DERIVATIONS:
        m = rule.pattern.match(field_id)
        if m:
            return rule, m
    return None


# --------------------------------------------------------------------------
# the schema
# --------------------------------------------------------------------------


class Schema:
    """The set of fields the system knows about, loaded from config/fields.json."""

    def __init__(self, raw: dict):
        problems: list[str] = []
        self.groups: dict[str, dict] = raw.get("groups", {})
        if not self.groups:
            problems.append("fields.json: no 'groups' defined")

        self.fields: dict[str, FieldDef] = {}
        self.order: list[str] = []
        for entry in raw.get("fields", []):
            fd = FieldDef.from_json(entry, problems)
            if fd is None:
                continue
            if fd.id in self.fields:
                problems.append(f"fields.json: {fd.id} is defined twice")
                continue
            if fd.group and fd.group not in self.groups:
                problems.append(f"fields.json: {fd.id} is in group {fd.group!r}, which is not declared in 'groups'")
            self.fields[fd.id] = fd
            self.order.append(fd.id)

        # Every derived field must be computable, and its source must exist.
        for fd in self.fields.values():
            if not fd.derived:
                continue
            hit = match_derivation(fd.id)
            if hit is None:
                problems.append(
                    f"fields.json: {fd.id} is marked derived but no rule in schema.py knows how to compute it "
                    f"(known rules: {', '.join(r.name for r in DERIVATIONS)})"
                )
                continue
            rule, m = hit
            candidates = rule.sources(m)
            if candidates and not any(src in self.fields for src in candidates):
                wanted = " or ".join(candidates)
                problems.append(f"fields.json: {fd.id} is derived from {wanted}, which is not defined")

        # depends_on must point at a real field.
        for fd in self.fields.values():
            if fd.depends_on and fd.depends_on not in self.fields:
                problems.append(f"fields.json: {fd.id} depends_on {fd.depends_on!r}, which is not defined")

        if problems:
            raise ConfigError(problems)

    # -- introspection -----------------------------------------------------

    def __contains__(self, field_id: str) -> bool:
        return field_id in self.fields

    def ids_for_groups(self, groups: Iterable[str]) -> set[str]:
        wanted = set(groups)
        return {fid for fid, fd in self.fields.items() if fd.group in wanted}

    def input_fields(self, groups: Iterable[str]) -> list[FieldDef]:
        """The fields a human types, in display order: by group order, then file order."""
        wanted = set(groups)
        chosen = [
            self.fields[fid]
            for fid in self.order
            if self.fields[fid].group in wanted and not self.fields[fid].derived
        ]
        return sorted(chosen, key=lambda fd: self.groups.get(fd.group, {}).get("order", 99))

    def group_order(self, groups: Iterable[str]) -> list[str]:
        wanted = [g for g in groups if g in self.groups]
        return sorted(wanted, key=lambda g: self.groups[g].get("order", 99))

    def unknown_groups(self, groups: Iterable[str]) -> list[str]:
        return [g for g in groups if g not in self.groups]

    # -- intake validation -------------------------------------------------

    def applicable(self, fd: FieldDef, values: dict[str, Any]) -> bool:
        """False when a field is gated behind an unchecked box (tribe, new name)."""
        if fd.depends_on is None:
            return True
        return as_bool(values.get(fd.depends_on))

    def on_questionnaire(self, fd: FieldDef) -> bool:
        """Whether to ask the adoptive family this. A field may opt out of a group
        that is otherwise theirs to answer — the fee summary is the office's."""
        if fd.questionnaire is not None:
            return fd.questionnaire
        return self.groups.get(fd.group, {}).get("questionnaire", True) is not False

    def answerable_label(self, field_id: str) -> str:
        """The question a person would have to answer to fill this in.

        A derived field has no question of its own — nobody types
        ``child1_birth_day``, they type the date of birth it is split from. When a
        gap has to be described to staff, describing the field they can actually
        act on is the difference between a useful note and an internal name.
        """
        fd = self.fields.get(field_id)
        if fd is None:
            return field_id
        if not fd.derived:
            return self.label_for(fd)

        hit = match_derivation(field_id)
        if hit is not None:
            rule, match = hit
            for source in rule.sources(match):
                if source in self.fields:
                    return self.answerable_label(source)
        return self.label_for(fd)

    def label_for(self, fd: FieldDef) -> str:
        """'Child — Date of birth'. Half a dozen fields are called 'Date of birth';
        an error message has to say which one."""
        group_label = self.groups.get(fd.group, {}).get("label")
        return f"{group_label} — {fd.label}" if group_label else fd.label

    def validate(
        self, values: dict[str, Any], groups: Iterable[str], *, include_required: bool = True
    ) -> list[str]:
        """Every problem with the entered data. Empty list means it is usable.

        ``include_required=False`` reports only answers that are *wrong* (bad date,
        option that does not exist), not answers that are merely absent — the
        review screen lists those separately, as something still to do rather than
        as a mistake.
        """
        problems: list[str] = []
        in_scope = self.ids_for_groups(groups)

        for key in values:
            if key not in self.fields:
                problems.append(f"{key}: not a field this system knows about")
            elif key not in in_scope:
                problems.append(f"{key}: not part of the selected variant")

        for fd in self.input_fields(groups):
            raw = values.get(fd.id)
            label = self.label_for(fd)
            if not self.applicable(fd, values):
                continue
            if is_blank(raw):
                if fd.required and include_required:
                    problems.append(f"{label}: required")
                continue
            if fd.type == "select" and str(raw) not in fd.options:
                problems.append(f"{label}: {raw!r} is not one of {', '.join(fd.options)}")
            elif fd.type == "date":
                try:
                    parse_date(raw)
                except ValueError as exc:
                    problems.append(f"{label}: {exc}")
            elif fd.type == "number":
                try:
                    float(str(raw))
                except ValueError:
                    problems.append(f"{label}: {raw!r} is not a number")

        return problems

    def missing_required(self, values: dict[str, Any], groups: Iterable[str]) -> list[str]:
        """Labels of required answers not yet given — for the review screen."""
        return [
            self.label_for(fd)
            for fd in self.input_fields(groups)
            if fd.required and self.applicable(fd, values) and is_blank(values.get(fd.id))
        ]

    # -- context construction ---------------------------------------------

    def build_context(
        self,
        values: dict[str, Any],
        groups: Iterable[str],
        settings: dict[str, Any] | None = None,
        today: date | None = None,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        """Turn entered values into the dict a template renders against.

        A field that does not apply (``tribe`` when ICWA is off) is left *out* of
        the context rather than blanked. Rendering uses GuardedUndefined, so a
        template that reaches for it outside its guard fails loudly instead of
        printing nothing where a name belongs.

        ``allow_missing`` is for drafting: an unanswered question stops being a
        reason to refuse, and the gap is filled in later by the engine with a
        visible marker naming the field. An answer that is *wrong* — a date that
        is not a date, an option that is not on the list — still refuses, because
        that is a mistake rather than a blank.
        """
        problems = self.validate(values, groups, include_required=not allow_missing)
        if problems:
            raise IntakeError(problems)

        today = today or date.today()
        in_scope = self.ids_for_groups(groups)
        ctx: dict[str, Any] = {_SETTINGS_KEY: dict(settings or {})}

        # 1. typed values, coerced and formatted
        for fd in self.input_fields(groups):
            raw = values.get(fd.id)
            if not self.applicable(fd, values) or is_blank(raw):
                if fd.type == "bool":
                    ctx[fd.id] = as_bool(raw)
                continue
            ctx[fd.id] = self._coerce(fd, raw)

        # 2. derived values, resolved to a fixpoint so a derivation may read another
        pending = {fid for fid in in_scope if self.fields[fid].derived}
        while pending:
            progressed = set()
            for fid in sorted(pending):
                hit = match_derivation(fid)
                assert hit is not None  # guaranteed by __init__ validation
                rule, m = hit
                candidates = rule.sources(m)
                if candidates and not any(src in ctx for src in candidates):
                    continue  # source not answered (or not yet derived)
                ctx[fid] = rule.compute(m, ctx, today)
                progressed.add(fid)
            if not progressed:
                break  # the rest depend on answers that were not given
            pending -= progressed

        # 3. declared defaults, rendered against everything computed so far
        for fid in sorted(in_scope):
            fd = self.fields[fid]
            if fd.default is None or not is_blank(ctx.get(fid)):
                continue
            ctx[fid] = _render_default(fd.default, ctx)

        ctx.pop(_SETTINGS_KEY, None)
        return {k: v for k, v in ctx.items() if not is_blank(v)}

    def _coerce(self, fd: FieldDef, raw: Any) -> Any:
        if fd.type == "bool":
            return as_bool(raw)
        if fd.type == "date":
            return format_date(parse_date(raw), fd.date_format)
        if fd.type == "number":
            text = str(raw).strip()
            return int(float(text)) if float(text).is_integer() else float(text)
        return str(raw).strip()


def _render_default(expr: str, ctx: dict[str, Any]) -> str:
    """Defaults are tiny Jinja strings, e.g. 'FA-{{ case_year }}-_____'."""
    from jinja2 import Environment, StrictUndefined

    return Environment(undefined=StrictUndefined).from_string(expr).render(**ctx)
