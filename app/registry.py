"""Loads config/, validates it against the templates on disk, exposes the catalog.

This is the safety net described in PROJECT_BRIEF.md. On launch it opens every
template referenced by ``matters.json``, asks docxtpl which variables it uses,
and asserts each one is a field declared in ``fields.json`` *and* reachable from
the field groups of the variant that renders it. Every problem found is reported
together; if there are any, the app refuses to start.

The failure this exists to prevent: a template with ``{{ petitoner1_name }}``
misspelled quietly renders a petition with a blank where a name belongs.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterable

from docxtpl import DocxTemplate
from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, meta

from app.schema import ConfigError, Schema

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def project_root() -> Path:
    """Where config/ and templates/ live.

    Frozen: beside the application, so staff can drop in a new template and edit
    matters.json without a rebuild — that expandability is the whole point.
    Source: the repository root.

    On macOS a packaged app is a bundle: the executable sits at
    ``Name.app/Contents/MacOS/Name``, so the plain parent directory is *inside*
    the bundle. Config left there would be invisible in Finder and thrown away by
    the next install, so the bundle is stepped out of to give the same
    arrangement as Windows — config/ and templates/ next to the thing you launch.
    """
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
        if beside.name == "MacOS" and beside.parent.name == "Contents":
            return beside.parent.parent.parent
        return beside
    return Path(__file__).resolve().parent.parent


ROOT = project_root()
CONFIG_DIR = ROOT / "config"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "output"
INTAKE_DIR = ROOT / "intake"


def ui_dir() -> Path:
    """Where index.html and client_form.html live.

    These travel *inside* the .exe — they are code, unlike config/ and templates/,
    which sit beside it so the office can edit them without a rebuild.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "ui"
    return Path(__file__).resolve().parent / "ui"


UI_DIR = ui_dir()


class GuardedUndefined(StrictUndefined):
    """A value that may be *tested for* but never *printed* when it is absent.

    ``{% if petitioner2_name %}`` is a fair question for a document filed in both
    one- and two-petitioner cases. ``{{ petitioner2_name }}`` outside that guard
    is not a question — it is a blank where a name belongs, so it still raises.

    StrictUndefined refuses both. Overriding only the boolean test keeps the
    fail-loud rule exactly where it matters.
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0


def jinja_env() -> Environment:
    """One environment for everything: templates, output names, defaults."""
    return Environment(undefined=GuardedUndefined)


# --------------------------------------------------------------------------
# catalog objects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Document:
    template: str
    output_name: str
    status: str = "ready"
    label: str = ""
    #: Groups this document may reference *in addition to* its variant's own.
    #: Used by a document filed in several variants that differ — the DHS consent
    #: names a second petitioner when there is one. Anything from an extra group
    #: may be absent at generation time, so the template must guard it with
    #: ``{% if %}``; printing it unguarded still fails (see GuardedUndefined).
    extra_field_groups: tuple[str, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


@dataclass
class Variant:
    id: str
    label: str
    matter_id: str
    status: str
    petitioners: int
    children: int
    field_groups: list[str]
    documents: list[Document]
    common_documents: list[Document] = dc_field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def all_documents(self) -> list[Document]:
        """Variant documents plus the matter-wide ones, ready only."""
        return [d for d in (*self.documents, *self.common_documents) if d.is_ready]


@dataclass
class Matter:
    id: str
    label: str
    variants: list[Variant]


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


class Registry:
    """The validated catalog. Construct with :meth:`load`."""

    def __init__(
        self,
        schema: Schema,
        matters: list[Matter],
        settings: dict[str, Any],
        notes: list[str],
        pending_templates: list[str] | None = None,
    ):
        self.schema = schema
        self.matters = matters
        self.settings = settings
        self.notes = notes  # short, non-fatal observations shown in the window
        self.pending_templates = pending_templates or []  # detail for the CLI

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, config_dir: Path | None = None, templates_dir: Path | None = None) -> "Registry":
        config_dir = config_dir or CONFIG_DIR
        templates_dir = templates_dir or TEMPLATES_DIR

        fields_raw = _read_json(config_dir / "fields.json")
        matters_raw = _read_json(config_dir / "matters.json")
        settings = _read_settings(config_dir / "settings.json")

        schema = Schema(fields_raw)  # raises ConfigError on a bad fields.json

        problems: list[str] = []
        pending: set[str] = set()
        matters = _parse_matters(matters_raw, problems)
        if problems:
            raise ConfigError(problems)

        _validate_templates(matters, schema, templates_dir, problems, pending)
        if problems:
            raise ConfigError(problems)

        notes = []
        if pending:
            notes.append(
                f"{len(pending)} template(s) named in matters.json have not been written yet; "
                f"the combinations that need them are hidden."
            )
        return cls(schema, matters, settings, notes, sorted(pending))

    # -- lookup ------------------------------------------------------------

    def matter(self, matter_id: str) -> Matter:
        for m in self.matters:
            if m.id == matter_id:
                return m
        raise KeyError(f"no such adoption type: {matter_id!r}")

    def variant(self, matter_id: str, variant_id: str) -> Variant:
        for v in self.matter(matter_id).variants:
            if v.id == variant_id:
                return v
        raise KeyError(f"no such variant: {matter_id}/{variant_id}")

    def template_path(self, relative: str) -> Path:
        return TEMPLATES_DIR / relative

    # -- for the UI --------------------------------------------------------

    def catalog(self, include_pending: bool = False) -> list[dict]:
        """The matter/variant tree, ready variants only unless asked otherwise."""
        out = []
        for m in self.matters:
            variants = [v for v in m.variants if v.is_ready or include_pending]
            if not variants:
                continue
            out.append(
                {
                    "id": m.id,
                    "label": m.label,
                    "variants": [
                        {
                            "id": v.id,
                            "label": v.label,
                            "status": v.status,
                            "petitioners": v.petitioners,
                            "children": v.children,
                            "documents": [
                                {"template": d.template, "output_name": d.output_name}
                                for d in v.all_documents()
                            ],
                        }
                        for v in variants
                    ],
                }
            )
        return out

    def form_spec(self, matter_id: str, variant_id: str) -> dict:
        """Everything index.html needs to draw the intake form. No hardcoded fields."""
        variant = self.variant(matter_id, variant_id)
        groups = self.schema.group_order(variant.field_groups)
        return {
            "matter": matter_id,
            "variant": variant_id,
            "label": variant.label,
            "groups": [
                {
                    "id": g,
                    "label": self.schema.groups[g].get("label", g),
                    "fields": [
                        {
                            "id": fd.id,
                            "label": fd.label,
                            "type": fd.type,
                            "required": fd.required,
                            "options": list(fd.options),
                            "help": fd.help,
                            "depends_on": fd.depends_on,
                        }
                        for fd in self.schema.input_fields([g])
                    ],
                }
                for g in groups
            ],
            "documents": [
                {"template": d.template, "output_name": d.output_name} for d in variant.all_documents()
            ],
        }


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError([f"missing config file: {path}"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError([f"{path.name} is not valid JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}"])


def _read_settings(path: Path) -> dict[str, Any]:
    """Office details (attorney block, etc). Absent is fine; templates that need
    a value will fail loudly at generation time with a message naming the field."""
    if not path.exists():
        return {}
    return {k: v for k, v in _read_json(path).items() if not k.startswith("_")}


def _document(raw: dict, where: str, problems: list[str]) -> Document | None:
    template = raw.get("template")
    if not template:
        problems.append(f"matters.json: {where} has a document with no 'template'")
        return None
    if not raw.get("output_name"):
        problems.append(f"matters.json: {where} document {template} has no 'output_name'")
        return None
    return Document(
        template=template,
        output_name=raw["output_name"],
        status=raw.get("status", "ready"),
        label=raw.get("label", ""),
        extra_field_groups=tuple(raw.get("field_groups", ())),
    )


def _parse_matters(raw: dict, problems: list[str]) -> list[Matter]:
    matters: list[Matter] = []
    seen_matters: set[str] = set()

    for m_raw in raw.get("matters", []):
        mid = m_raw.get("id")
        if not mid:
            problems.append("matters.json: a matter has no 'id'")
            continue
        if mid in seen_matters:
            problems.append(f"matters.json: matter {mid!r} is defined twice")
            continue
        seen_matters.add(mid)

        common = [
            d
            for d in (_document(d_raw, f"matter {mid}", problems) for d_raw in m_raw.get("common_documents", []))
            if d is not None
        ]

        variants: list[Variant] = []
        seen_variants: set[str] = set()
        for v_raw in m_raw.get("variants", []):
            vid = v_raw.get("id")
            if not vid:
                problems.append(f"matters.json: a variant of {mid} has no 'id'")
                continue
            if vid in seen_variants:
                problems.append(f"matters.json: variant {vid!r} is defined twice")
                continue
            seen_variants.add(vid)

            docs = [
                d
                for d in (_document(d_raw, f"variant {vid}", problems) for d_raw in v_raw.get("documents", []))
                if d is not None
            ]
            if not docs:
                problems.append(f"matters.json: variant {vid} generates no documents")

            variants.append(
                Variant(
                    id=vid,
                    label=v_raw.get("label", vid),
                    matter_id=mid,
                    status=v_raw.get("status", "ready"),
                    petitioners=int(v_raw.get("petitioners", 1)),
                    children=int(v_raw.get("children", 1)),
                    field_groups=list(v_raw.get("field_groups", [])),
                    documents=docs,
                    common_documents=common,
                )
            )

        if not variants:
            problems.append(f"matters.json: matter {mid} has no variants")
        matters.append(Matter(id=mid, label=m_raw.get("label", mid), variants=variants))

    if not matters:
        problems.append("matters.json: no matters defined")
    return matters


# --------------------------------------------------------------------------
# template validation — the safety net
# --------------------------------------------------------------------------


def template_variables(path: Path) -> set[str]:
    """Every Jinja variable a .docx template reads, including inside conditionals."""
    doc = DocxTemplate(str(path))
    return set(doc.get_undeclared_template_variables(jinja_env()))


def string_variables(text: str) -> set[str]:
    env = jinja_env()
    return set(meta.find_undeclared_variables(env.parse(text)))


def _validate_templates(
    matters: Iterable[Matter],
    schema: Schema,
    templates_dir: Path,
    problems: list[str],
    pending: set[str],
) -> None:
    for matter in matters:
        for variant in matter.variants:
            unknown_groups = schema.unknown_groups(variant.field_groups)
            if unknown_groups:
                problems.append(
                    f"matters.json: variant {variant.id} lists field group(s) "
                    f"{', '.join(unknown_groups)} that fields.json does not define"
                )
                continue

            for doc in (*variant.documents, *variant.common_documents):
                path = templates_dir / doc.template
                ready = variant.is_ready and doc.is_ready

                unknown_extra = schema.unknown_groups(doc.extra_field_groups)
                if unknown_extra:
                    problems.append(
                        f"matters.json: {doc.template} lists field group(s) "
                        f"{', '.join(unknown_extra)} that fields.json does not define"
                    )
                    continue
                available = schema.ids_for_groups([*variant.field_groups, *doc.extra_field_groups])

                if not path.exists():
                    if ready:
                        problems.append(f"{variant.id}: template not found: {path}")
                    else:
                        pending.add(doc.template)
                    continue

                try:
                    used = template_variables(path)
                except TemplateSyntaxError as exc:
                    problems.append(f"{doc.template}: Jinja syntax error on line {exc.lineno}: {exc.message}")
                    continue
                except Exception as exc:  # a corrupt or non-docx file
                    problems.append(f"{doc.template}: could not be read as a .docx template ({exc})")
                    continue

                try:
                    used |= string_variables(doc.output_name)
                except TemplateSyntaxError as exc:
                    problems.append(f"matters.json: output_name {doc.output_name!r} is not valid Jinja: {exc.message}")

                for var in sorted(used):
                    if var not in schema:
                        near = _nearest(var, schema.fields)
                        hint = f" (did you mean {near}?)" if near else ""
                        problems.append(f"{doc.template}: uses {{{{ {var} }}}}, which fields.json does not define{hint}")
                    elif var not in available:
                        group = schema.fields[var].group
                        problems.append(
                            f"{doc.template}: uses {{{{ {var} }}}} (group '{group}'), but variant "
                            f"{variant.id} only collects: {', '.join(variant.field_groups)}. "
                            f"Either add '{group}' to that variant's field_groups, or — if the value is "
                            f"genuinely optional here — add \"field_groups\": [\"{group}\"] to this "
                            f"document in matters.json and guard it in the template with {{% if {var} %}}."
                        )

                if not ready:
                    pending.add(doc.template)


def _nearest(name: str, candidates: Iterable[str]) -> str | None:
    """Typo hint. A misspelled variable is the failure this whole module exists for."""
    import difflib

    hits = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.8)
    return hits[0] if hits else None
