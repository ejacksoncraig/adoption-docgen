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
import os
import shutil
import sys
import tempfile
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


#: The marker macOS puts in the path of a translocated application. Apple's own
#: name for the directory; there is no API for this that works from Python.
_TRANSLOCATION_MARKER = "/AppTranslocation/"


def translocated() -> bool:
    """Whether macOS is running the application from a relocated copy.

    A downloaded app carries a quarantine flag, and macOS launches a quarantined
    app from a randomised read-only mount rather than from where it actually
    sits — "App Translocation", or Gatekeeper path randomisation. Only the
    ``.app`` is carried across. Everything beside it is left behind.

    That is fatal here specifically *because* config/ and templates/ live beside
    the bundle by design, so a translocated launch sees neither, and cannot
    write output/ either — the mount is read-only. The failure arrives as a
    configuration error listing missing files, which sends whoever hit it
    looking for a problem in config/ that does not exist.

    The condition is cleared by approving the app once in System Settings →
    Privacy & Security, which is what a person should be told to do. Moving the
    folder does not clear it; that was tested.
    """
    for path in (sys.executable, getattr(sys, "_MEIPASS", "")):
        if path and _TRANSLOCATION_MARKER in str(path):
            return True
    return False


#: The name the application is known by outside its own folder.
APP_NAME = "Adoption Filing Generator"


def bundled_defaults() -> Path | None:
    """config/ and templates/ as they shipped, carried *inside* the application.

    These are a fallback, not the working copy. The working copy is meant to sit
    beside the application where the office can drop a new .docx into it, and
    that stays true. But an application that is only the .app — dragged out of
    the folder it arrived in, or relocated by macOS — used to be an application
    with no templates at all, which is a confusing way to discover a rule about
    where files must be kept.
    """
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", "")
    if not base:
        return None
    defaults = Path(base) / "defaults"
    return defaults if defaults.is_dir() else None


def user_data_dir() -> Path:
    """Per-user working copy, used when there is none beside the application.

    Deliberately *not* Documents. Documents is the obvious choice for somewhere
    a person can find, and the wrong one here: it is what "Desktop & Documents"
    syncing uploads to iCloud, and this application writes adoption filings. The
    generated documents are reachable from the button on the result screen
    instead, which does not require knowing where they are.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_NAME


#: Places the system empties without asking. An application running from one of
#: these has not been installed — it is being *previewed*: opened straight out
#: of a .zip, or relocated by Gatekeeper. Both put a real-looking config/ and
#: templates/ next to the program, and both take the folder away again, which
#: surfaces as "No such file or directory: /private/var/folders/..." at the
#: moment somebody generates a filing rather than at startup.
_TEMPORARY_MARKERS = ("/AppTranslocation/", "/private/var/folders/", "/var/folders/")


def is_temporary(path: Path) -> bool:
    """Whether a path is somewhere the system will delete without warning."""
    text = str(path.resolve() if path.exists() else path)
    if any(marker in text for marker in _TEMPORARY_MARKERS):
        return True
    return text.startswith(str(Path(tempfile.gettempdir()).resolve()))


def data_root() -> Path:
    """Where config/, templates/, output/ and intake/ are actually read.

    Beside the application when they are really there — the arrangement the
    office is told about, and the one a rebuild preserves. Otherwise a per-user
    folder, seeded from the copies inside the application, so that a bare .app
    still runs rather than reporting its own templates missing.

    "Really there" excludes a temporary copy. Files beside a program running out
    of a .zip or a Gatekeeper relocation look exactly like an installation and
    are gone by the time anything is written to them, so they are refused in
    favour of somewhere that will still exist.
    """
    if not getattr(sys, "frozen", False):
        return project_root()
    beside = project_root()
    if (beside / "config" / "matters.json").is_file() and not is_temporary(beside):
        return beside
    return user_data_dir()


ROOT = data_root()
CONFIG_DIR = ROOT / "config"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "output"
INTAKE_DIR = ROOT / "intake"


def prepare_data() -> list[str]:
    """Put a working copy in place if there is not one, and say what happened.

    Called once at startup, before the configuration is read. Copies only what
    is missing: an existing config/ is somebody's settings and templates/ may
    hold a template they wrote, and neither is replaced by the shipped version.
    """
    notes: list[str] = []
    defaults = bundled_defaults()
    if defaults is None or ROOT != user_data_dir():
        return notes

    first_time = not CONFIG_DIR.exists() and not TEMPLATES_DIR.exists()
    for name in ("config", "templates"):
        target, source = ROOT / name, defaults / name
        if target.exists() or not source.is_dir():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)

    if first_time:
        notes.append(
            f"The templates and settings this application came with have been put in "
            f"{ROOT}, because there were none beside the application itself."
        )
    if is_temporary(project_root()):
        notes.append(
            "This copy is running from a temporary folder, which is what happens when "
            "an application is opened straight out of a .zip, or before macOS has been "
            "told to trust it. Anything kept beside it there will be deleted without "
            "warning, so it is being ignored. Move the application somewhere real — "
            "Applications, or Documents — and open it from there."
        )
    if translocated():
        notes.append(
            "macOS is running this application from a temporary copy, which is what "
            "it does until an application is approved. It works, but approving it in "
            "System Settings > Privacy & Security is worth doing: until then it "
            "cannot see files kept beside it."
        )
    return notes


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
    #: A bool field that must be true for this document to be generated at all —
    #: the Notice to Tribe has nothing to say in a filing where ICWA does not
    #: apply, so it is skipped rather than rendered with blank/inapplicable
    #: content. Same name as a field's own ``depends_on``; checked against the
    #: intake values at generation time, not against the rendered context.
    depends_on: str | None = None

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
                            "searchable": fd.searchable,
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


# --------------------------------------------------------------------------
# the office's own details, edited in the application rather than by hand
# --------------------------------------------------------------------------

#: How to *ask* for each office setting: the key, the question, and a hint.
#:
#: This lives here rather than in settings.json because it describes the asking,
#: not the value, and settings.json stays a plain key/value file that a person
#: can still open in a text editor. It is not a closed list: any key found in
#: settings.json and not named here is offered for editing too, appended after
#: these, so the file remains the source of truth for what exists.
OFFICE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("attorney_short_name", "Attorney name",
     "As it should read in the body of a filing — for example Terri Craig."),
    ("attorney_full_name", "Full legal name",
     "For the signature block, if it differs from the name above."),
    ("attorney_oba", "OBA number", "Oklahoma Bar Association number."),
    ("attorney_firm", "Firm", "The firm or office name."),
    ("attorney_address", "Street address", ""),
    ("attorney_city_state_zip", "City, state and ZIP", ""),
    ("attorney_phone", "Phone", ""),
    ("attorney_email", "Email", ""),
)


def settings_path(config_dir: Path | None = None) -> Path:
    return (config_dir or CONFIG_DIR) / "settings.json"


def office_fields(schema: Schema, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """The office settings to offer for editing, with their current values.

    ``used`` says whether any template can actually print this value: a setting
    is only reachable if fields.json declares a derived field of the same name.
    A key nobody reads is still shown — it is in the file, and deleting it is
    not this screen's decision — but it is not presented as though filling it in
    will change a document.
    """
    known = {key for key, _, _ in OFFICE_FIELDS}
    extra = [k for k in settings if not k.startswith("_") and k not in known]

    rows = []
    for key, label, hint in OFFICE_FIELDS + tuple((k, k, "") for k in sorted(extra)):
        declared = schema.fields.get(key)
        rows.append({
            "id": key,
            "label": label,
            "hint": hint,
            "value": str(settings.get(key, "") or ""),
            "used": bool(declared is not None and declared.derived),
        })
    return rows


def write_settings(values: dict[str, str], config_dir: Path | None = None) -> Path:
    """Save the office details, keeping everything else in the file intact.

    The file is a person's to edit as much as it is the program's: the leading
    ``_comment`` explains what it is for, and a hand-added key is somebody's
    intention. So this reads what is there, replaces only the keys it was given,
    and writes the whole thing back.

    Written to a temporary file and moved into place, because the alternative to
    an atomic write here is a settings.json truncated to nothing by a crash
    mid-save — which stops the application starting.
    """
    path = settings_path(config_dir)
    existing: dict[str, Any] = {}
    if path.exists():
        existing = _read_json(path)

    for key, value in values.items():
        if key.startswith("_"):
            continue                     # never let the UI rewrite the comment
        existing[key] = str(value).strip()

    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ConfigError([
            f"Could not save {path.name}: {exc}",
            "If the application is inside a read-only folder — still in the disk "
            "image it arrived in, say — move it out and try again.",
        ]) from exc
    return path


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
        depends_on=raw.get("depends_on"),
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

                if doc.depends_on is not None:
                    gate = schema.fields.get(doc.depends_on)
                    if gate is None:
                        problems.append(
                            f"matters.json: {doc.template} depends_on {doc.depends_on!r}, "
                            f"which fields.json does not define"
                        )
                    elif gate.type != "bool":
                        problems.append(
                            f"matters.json: {doc.template} depends_on {doc.depends_on!r}, "
                            f"which is not a bool field"
                        )
                    elif doc.depends_on not in available:
                        problems.append(
                            f"matters.json: {doc.template} depends_on {doc.depends_on!r} (group "
                            f"'{gate.group}'), but variant {variant.id} only collects: "
                            f"{', '.join(variant.field_groups)}"
                        )

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
