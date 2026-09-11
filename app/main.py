"""Entry point: a pywebview window around app/ui/index.html.

The bridge below is the only path between the UI and the engine. Every method
returns a plain dict — ``{"ok": true, ...}`` or ``{"ok": false, "problems": [...]}``
— so a failure always arrives at the screen as a sentence a person can read,
never as a stack trace or, worse, a silently empty document.

Nothing here opens a socket. The window loads a local file; there is no server
and no outbound request anywhere in this application.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app import engine, intake, responses, signature

if TYPE_CHECKING:                       # for the annotation below only
    import webview
from app.registry import (
    INTAKE_DIR,
    OUTPUT_DIR,
    UI_DIR,
    Registry,
    office_fields,
    prepare_data,
    settings_path,
    write_settings,
)
from app.schema import ConfigError, IntakeError, as_bool

WINDOW_TITLE = "Adoption Filing Generator"


def _fail(problems: list[str], **extra: Any) -> dict:
    return {"ok": False, "problems": problems, **extra}


def guarded(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Turn any exception into a displayable result. The window never dies."""

    def wrapper(*args, **kwargs) -> dict:
        try:
            return fn(*args, **kwargs)
        except (IntakeError, engine.RenderError, ConfigError) as exc:
            return _fail(exc.problems)
        except KeyError as exc:
            return _fail([str(exc.args[0]) if exc.args else "missing key"])
        except (ValueError, OSError) as exc:
            return _fail([str(exc)])
        except Exception as exc:  # unexpected — show it rather than hiding it
            traceback.print_exc()
            return _fail([f"Unexpected error: {exc.__class__.__name__}: {exc}"])

    wrapper.__name__ = fn.__name__
    return wrapper


class Api:
    """Methods callable from JavaScript as ``pywebview.api.<name>(payload)``.

    The same object serves the browser front end over HTTP; see app/server.py.
    Only the file-dialog methods differ between the two, so there is one
    implementation of generating, reviewing and importing, and no way for the
    window and the browser to disagree about what the app does.
    """

    #: Which front end this is talking to. The browser cannot open a native file
    #: dialog, so the page offers its own picker instead; see BrowserApi.
    mode = "desktop"

    #: pywebview is imported where it is used, not at the top of this module.
    #: app/server.py reuses this class to serve a browser, and that may be running
    #: somewhere with no GUI toolkit at all — a container, a Codespace — where
    #: importing a windowing library on the way past would stop it dead.

    def __init__(self, registry: Registry):
        self.registry = registry
        self._window: webview.Window | None = None

    # -- catalog and form -------------------------------------------------

    @guarded
    def bootstrap(self, _payload: dict | None = None) -> dict:
        settings = self.registry.settings
        return {
            "ok": True,
            "catalog": self.registry.catalog(),
            "notes": self.registry.notes,
            "output_dir": str(OUTPUT_DIR),
            "intake_dir": str(INTAKE_DIR),
            "pdf_available": engine.find_soffice() is not None,
            "attorney_configured": bool(settings.get("attorney_short_name")),
            "signature": signature.describe(),
            "saved_intakes": intake.list_intakes(),
            "mode": self.mode,
        }

    # -- the office's own details -----------------------------------------

    @guarded
    def settings(self, _payload: dict | None = None) -> dict:
        """The office details, as questions with their current answers."""
        return {
            "ok": True,
            "fields": office_fields(self.registry.schema, self.registry.settings),
            "path": str(settings_path()),
            "signature": self._signature(),
        }

    @guarded
    def save_settings(self, payload: dict) -> dict:
        """Write the office details, then reload so the next filing uses them.

        Reloading matters: the settings are read once at startup and folded into
        every derived attorney_ field, so without this the screen would report
        success while documents kept printing the old signature block until the
        application was restarted.
        """
        write_settings(payload.get("values") or {})
        self.registry = Registry.load()
        return {
            "ok": True,
            "fields": office_fields(self.registry.schema, self.registry.settings),
            "attorney_configured": bool(self.registry.settings.get("attorney_short_name")),
        }

    # -- the attorney's signature -----------------------------------------

    def _signature(self) -> dict:
        """What is on file, with the image itself for the screen to show.

        The preview is inlined as a data URL rather than served from a path: the
        page is loaded from a file:// URL in the desktop window and over
        loopback in the browser, and only one of those can read config/.
        """
        described = signature.describe()
        path = described.get("path")
        if path:
            import base64

            blob = Path(path).read_bytes()
            kind = Path(path).suffix.lstrip(".").replace("jpg", "jpeg")
            described["preview"] = f"data:image/{kind};base64,{base64.b64encode(blob).decode()}"
        return described

    def _reload(self) -> None:
        """Re-read config so the next filing sees what just changed on disk."""
        self.registry = Registry.load()

    @guarded
    def signature(self, _payload: dict | None = None) -> dict:
        return {"ok": True, "signature": self._signature()}

    @guarded
    def save_signature(self, payload: dict) -> dict:
        """Store an uploaded image, or one picked with a native dialog."""
        import base64

        blob = b""
        if payload.get("data"):
            try:
                blob = base64.b64decode(payload["data"], validate=True)
            except Exception:
                return _fail(["That file could not be read."])
        elif payload.get("path"):
            source = Path(str(payload["path"]))
            if not source.is_file():
                return _fail([f"There is no file at {source}."])
            blob = source.read_bytes()
        else:
            return _fail(["No image was given."])

        try:
            signature.save(blob)
        except signature.SignatureError as exc:
            return _fail(exc.problems)
        self._reload()
        return {"ok": True, "signature": self._signature()}

    @guarded
    def choose_signature(self, _payload: dict | None = None) -> dict:
        if self._window is None:
            return _fail(["No window available to open a file dialog."])
        import webview

        chosen = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Signature image (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.tiff)",
                        "All files (*.*)"),
        )
        if not chosen:
            return {"ok": True, "cancelled": True}
        path = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
        return self.save_signature({"path": path})

    @guarded
    def remove_signature(self, _payload: dict | None = None) -> dict:
        signature.remove()
        self._reload()
        return {"ok": True, "signature": self._signature()}

    # -- catalog and form -------------------------------------------------

    @guarded
    def form_spec(self, payload: dict) -> dict:
        spec = self.registry.form_spec(payload["matter"], payload["variant"])
        return {"ok": True, "spec": spec}

    @guarded
    def random_intake(self, payload: dict) -> dict:
        """A complete, obviously-fake intake for trying the program out.

        Different every time, so successive runs exercise different branches of
        the templates — ICWA on then off, relinquished then terminated, a name
        change and none. Every name carries a SAMPLE prefix: these documents are
        otherwise indistinguishable from real ones once they are in the output
        folder.
        """
        variant = self.registry.variant(payload["matter"], payload["variant"])
        values = intake.random_values(self.registry.schema, variant.field_groups)
        return {"ok": True, "values": values, "count": len(values)}

    # -- review ------------------------------------------------------------

    @guarded
    def review(self, payload: dict) -> dict:
        """What would happen if Generate were pressed right now."""
        matter, variant_id = payload["matter"], payload["variant"]
        values = _clean(payload.get("values") or {})
        variant = self.registry.variant(matter, variant_id)
        schema = self.registry.schema

        missing = schema.missing_required(values, variant.field_groups)
        problems = schema.validate(values, variant.field_groups, include_required=False)

        applicable_docs = [
            doc for doc in variant.all_documents()
            if doc.depends_on is None or as_bool(values.get(doc.depends_on))
        ]

        documents: list[str] = []
        if not problems:
            context = schema.build_context(
                values, variant.field_groups, self.registry.settings, allow_missing=True
            )
            for doc in applicable_docs:
                try:
                    name = engine.resolve_output_name(doc, context)
                except engine.RenderError:
                    name = doc.output_name
                documents.append((engine.DRAFT_PREFIX if missing else "") + name)
        else:
            documents = [doc.output_name for doc in applicable_docs]

        return {
            "ok": True,
            "missing": missing,
            "problems": problems,
            "documents": documents,
            # Unanswered questions no longer block; wrong answers still do.
            "draft": bool(missing),
            "blocked": bool(problems),
        }

    # -- generation --------------------------------------------------------

    @guarded
    def generate(self, payload: dict) -> dict:
        """Generate the filing, finished or not.

        An unanswered question never blocks: what it produces instead is a draft,
        with every gap printed as a visible marker and every file named DRAFT.
        Staff can take an unfinished petition away to work on. What they cannot do
        is end up with a document that looks complete and is not.
        """
        matter, variant_id = payload["matter"], payload["variant"]
        values = _clean(payload.get("values") or {})
        variant = self.registry.variant(matter, variant_id)

        missing = self.registry.schema.missing_required(values, variant.field_groups)
        result = engine.generate(
            self.registry, matter, variant_id, values,
            pdf=bool(payload.get("pdf")), draft=bool(missing),
            sign=as_bool(payload.get("sign", True)),
        )
        return {"ok": True, "result": result.as_dict()}

    @guarded
    def open_folder(self, payload: dict) -> dict:
        engine.open_folder(Path(payload["path"]))
        return {"ok": True}

    # -- intake files ------------------------------------------------------

    @guarded
    def save_intake(self, payload: dict) -> dict:
        values = _clean(payload.get("values") or {})
        matter, variant_id = payload["matter"], payload["variant"]
        target = payload.get("path")
        if not target and self._window is not None:
            import webview

            suggested = intake.default_intake_name(matter, variant_id, values)
            chosen = self._window.create_file_dialog(
                webview.SAVE_DIALOG, directory=str(INTAKE_DIR), save_filename=suggested
            )
            if not chosen:
                return {"ok": True, "cancelled": True}
            target = chosen if isinstance(chosen, str) else chosen[0]
        path = intake.save_intake(matter, variant_id, values, path=Path(target) if target else None)
        return {"ok": True, "path": str(path)}

    @guarded
    def choose_intake(self, _payload: dict | None = None) -> dict:
        if self._window is None:
            return _fail(["No window available to open a file dialog."])
        import webview

        chosen = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=str(INTAKE_DIR),
            allow_multiple=False,
            file_types=("Saved intake or client answers (*.json)", "All files (*.*)"),
        )
        if not chosen:
            return {"ok": True, "cancelled": True}
        return self.load_intake({"path": chosen[0] if isinstance(chosen, (list, tuple)) else chosen})

    @guarded
    def load_intake(self, payload: dict) -> dict:
        """Open a saved intake, or the answers a family sent back.

        Splits what is still missing in two: the questions the office fills in,
        and the ones the family left blank. Those need different actions — one is
        typing, the other is a phone call — so they are not run together into one
        list of things that are wrong.
        """
        loaded = intake.load_intake(Path(payload["path"]), self.registry)
        variant = self.registry.variant(loaded["matter"], loaded["variant"])
        schema = self.registry.schema

        missing = set(schema.missing_required(loaded["values"], variant.field_groups))
        for_family = {
            schema.label_for(fd)
            for fd in schema.input_fields(variant.field_groups)
            if schema.on_questionnaire(fd)
        }
        return {
            "ok": True,
            **loaded,
            "path": payload["path"],
            "staff_remaining": sorted(missing - for_family),
            "family_remaining": sorted(missing & for_family),
        }

    # -- a form the client already filled in --------------------------------

    @guarded
    def choose_response(self, payload: dict) -> dict:
        """Pick a form export and work out which column is which field.

        Nothing is imported yet. This returns what the app *thinks* the columns
        mean, for staff to check, because a column quietly read into the wrong
        field is a wrong name in a petition and nothing on screen would say so.
        """
        if self._window is None:
            return _fail(["No window available to open a file dialog."])
        import webview

        chosen = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=str(INTAKE_DIR),
            allow_multiple=False,
            file_types=("Client's completed form (*.csv;*.tsv;*.pdf)", "All files (*.*)"),
        )
        if not chosen:
            return {"ok": True, "cancelled": True}
        path = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
        return self.read_response({**payload, "path": path})

    @guarded
    def read_response(self, payload: dict) -> dict:
        matter, variant_id = payload["matter"], payload["variant"]
        variant = self.registry.variant(matter, variant_id)
        schema = self.registry.schema

        remembered = responses.load_mapping()
        sheet = responses.read_any(Path(payload["path"]), schema, variant.field_groups, remembered)
        columns = responses.match_columns(sheet, schema, variant.field_groups, remembered)
        return {
            "ok": True,
            "path": str(sheet.path),
            "name": sheet.path.name,
            "columns": [column.as_dict() for column in columns],
            "rows": sheet.row_labels(),
            "choices": self._field_choices(variant.field_groups),
        }

    @guarded
    def import_response(self, payload: dict) -> dict:
        """Apply the mapping staff confirmed and hand back the answers."""
        matter, variant_id = payload["matter"], payload["variant"]
        variant = self.registry.variant(matter, variant_id)
        schema = self.registry.schema

        sheet = responses.read_any(
            Path(payload["path"]), schema, variant.field_groups, responses.load_mapping()
        )
        confirmed = payload.get("mapping") or {}
        columns = [
            responses.Column(
                index=index,
                header=header,
                samples=sheet.samples(index),
                field_id=confirmed.get(str(index)) or None,
                confidence="remembered",
            )
            for index, header in enumerate(sheet.headers)
        ]

        values, notes = responses.to_values(sheet, int(payload.get("row", 0)), columns, schema)
        if payload.get("remember", True):
            responses.save_mapping(columns)

        missing = set(schema.missing_required(values, variant.field_groups))
        for_family = {
            schema.label_for(fd)
            for fd in schema.input_fields(variant.field_groups)
            if schema.on_questionnaire(fd)
        }
        return {
            "ok": True,
            "values": values,
            "notes": notes,
            "answered": sorted(values),
            "staff_remaining": sorted(missing - for_family),
            "family_remaining": sorted(missing & for_family),
        }

    def _field_choices(self, groups: list[str]) -> list[dict]:
        """The dropdown staff pick from when a column guessed wrong.

        Every option carries its group. Six fields are called "Date of birth", and
        this dropdown is the one place where telling the parent's from the child's
        actually matters — an unqualified list would hide the very mistake the
        screen exists to catch.
        """
        schema = self.registry.schema
        return [
            {
                "label": schema.groups[group_id].get("label", group_id),
                "fields": [
                    {"id": fd.id, "label": fd.label, "qualified": schema.label_for(fd)}
                    for fd in schema.input_fields([group_id])
                ],
            }
            for group_id in schema.group_order(groups)
        ]

    # -- questionnaire -----------------------------------------------------

    @guarded
    def questionnaire(self, payload: dict) -> dict:
        """The paper questionnaire: print it, fill it in by hand, type it back in."""
        matter, variant_id = payload["matter"], payload["variant"]
        default = intake.default_questionnaire_path(matter, variant_id)
        target = self._ask_where_to_save(default)
        if target is None:
            return {"ok": True, "cancelled": True}
        path = intake.build_questionnaire(self.registry, matter, variant_id, target)
        return {"ok": True, "path": str(path)}

    @guarded
    def worksheet(self, payload: dict) -> dict:
        """The office's own worksheet: every question, blank, to take notes on."""
        matter, variant_id = payload["matter"], payload["variant"]
        default = intake.default_worksheet_path(matter, variant_id)
        target = self._ask_where_to_save(default)
        if target is None:
            return {"ok": True, "cancelled": True}
        path = intake.build_intake_worksheet(self.registry, matter, variant_id, target)
        return {"ok": True, "path": str(path)}

    def _ask_where_to_save(self, default: Path) -> Path | None:
        """None means the dialog was cancelled."""
        if self._window is None:
            return default
        import webview

        chosen = self._window.create_file_dialog(
            webview.SAVE_DIALOG, directory=str(default.parent), save_filename=default.name
        )
        if not chosen:
            return None
        return Path(chosen if isinstance(chosen, str) else chosen[0])


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    """Drop keys the form left empty, so 'not answered' and '' mean the same thing."""
    return {k: v for k, v in values.items() if v is not None and v != ""}


# --------------------------------------------------------------------------
# startup
# --------------------------------------------------------------------------

_ERROR_PAGE = """
<!doctype html><meta charset="utf-8">
<style>
  body {{ font: 15px/1.55 "Segoe UI", system-ui, sans-serif; margin: 0; padding: 36px 40px;
          background: #fbfaf8; color: #2c2a26; }}
  h1 {{ font-size: 19px; margin: 0 0 6px; color: #8c2f28; }}
  p  {{ max-width: 62ch; color: #57534c; }}
  li {{ margin-bottom: 8px; font-family: Consolas, monospace; font-size: 13px; }}
  code {{ background: #efece6; padding: 1px 5px; border-radius: 3px; }}
</style>
<h1>The application cannot start</h1>
<p>Its configuration does not match the templates on disk. Nothing was generated.
   Fix the items below in <code>config/</code> or <code>templates/</code>, then start
   the application again.</p>
<ul>{items}</ul>
"""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def show_config_error(exc: ConfigError) -> None:
    """A validation failure is a window with a list, not a traceback in a console
    the user will never see. This is the message that saves a bad filing."""
    import webview

    for problem in exc.problems:
        print(f"  - {problem}", file=sys.stderr)
    items = "".join(f"<li>{_escape(p)}</li>" for p in exc.problems)
    webview.create_window(f"{WINDOW_TITLE} — configuration error",
                          html=_ERROR_PAGE.format(items=items), width=900, height=620)
    webview.start()


#: Everything the application needs at some point, including what it imports only
#: when the moment comes. pypdf is loaded the first time a client's form arrives
#: as a PDF — months after packaging, in front of somebody — so "it starts up
#: fine" proves nothing about it.
REQUIRED_MODULES = ("docx", "docxtpl", "jinja2", "lxml", "pypdf", "webview")


def self_check() -> int:
    """Import every dependency and report whether any is missing.

    Run by build.py against the built .exe. A packaged application that is missing
    a lazily-imported library looks perfectly healthy until the day someone uses
    the feature that needs it, which is the worst possible time to find out.
    """
    missing = []
    for name in REQUIRED_MODULES:
        try:
            __import__(name)
        except Exception as exc:                       # noqa: BLE001 - report them all
            missing.append(f"{name}: {exc}")

    for name in missing:
        print(f"missing dependency: {name}", file=sys.stderr)
    if missing:
        print("self-check: incomplete", file=sys.stderr)
        return 1

    problem = _render_check()
    if problem:
        print(f"cannot generate: {problem}", file=sys.stderr)
        print("self-check: incomplete", file=sys.stderr)
        return 1

    print("self-check: all dependencies present, and a document rendered", file=sys.stderr)
    return 0


def _render_check() -> str | None:
    """Actually generate a document, into a temporary folder that is thrown away.

    Importing a library is not the same as it working. python-docx carries data
    files of its own — ``docx/templates/default-header.xml`` and friends — which
    an import never touches and which a packaged build can leave behind or leave
    unreachable. That failure surfaces the first time somebody presses Generate,
    which is the worst possible moment and the furthest from anyone who can fix
    it. Rendering here moves it to the build.
    """
    import tempfile

    from app import engine
    from app.registry import Registry, bundled_defaults, prepare_data
    from app import intake as intake_module

    try:
        prepare_data()
        defaults = bundled_defaults()
        if defaults is not None and (defaults / "config").is_dir():
            registry = Registry.load(defaults / "config", defaults / "templates")
        else:
            registry = Registry.load()

        matter = registry.matters[0]
        variant = matter.variants[0]
        values = intake_module.random_values(registry.schema, variant.field_groups, seed=1)
        # Generation refuses without an attorney of record, which is right, and
        # is not what is being tested here.
        registry.settings = dict(registry.settings)
        registry.settings.setdefault("attorney_short_name", "SELF CHECK")
        if not registry.settings["attorney_short_name"]:
            registry.settings["attorney_short_name"] = "SELF CHECK"

        with tempfile.TemporaryDirectory() as scratch:
            engine.generate(registry, matter.id, variant.id, values,
                            output_root=Path(scratch))
    except Exception as exc:                           # noqa: BLE001 - report it
        return f"{exc.__class__.__name__}: {exc}"
    return None


def where() -> int:
    """Print which files this copy is actually using.

    Support for an installation on somebody else's computer otherwise means
    asking them to guess. Every question that has come up so far — are the
    templates being found, is macOS running this from somewhere else, which
    settings file is being written — is answered by this list.
    """
    from app import registry

    print(f"application  {sys.executable}")
    print(f"frozen       {bool(getattr(sys, 'frozen', False))}")
    print(f"relocated    {registry.translocated()}   (macOS App Translocation)")
    beside = registry.project_root()
    print(f"beside it    {beside}"
          f"{'   TEMPORARY - ignored' if registry.is_temporary(beside) else ''}")
    print(f"defaults in  {registry.bundled_defaults()}")
    print(f"per-user     {registry.user_data_dir()}")
    print(f"USING        {registry.ROOT}")
    print(f"  config     {registry.CONFIG_DIR}  {'ok' if registry.CONFIG_DIR.is_dir() else 'MISSING'}")
    print(f"  templates  {registry.TEMPLATES_DIR}  {'ok' if registry.TEMPLATES_DIR.is_dir() else 'MISSING'}")
    print(f"  output     {registry.OUTPUT_DIR}")
    print(f"  intake     {registry.INTAKE_DIR}")
    return 0


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()
    if "--where" in sys.argv:
        return where()

    # Before the configuration is read: this is what puts a working copy in
    # place when there is none beside the application, which used to be a
    # configuration error listing every template as missing.
    notes = prepare_data()

    try:
        registry = Registry.load()
    except ConfigError as exc:
        show_config_error(exc)
        return 2
    registry.notes = list(notes) + list(registry.notes)

    import webview

    for directory in (OUTPUT_DIR, INTAKE_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    api = Api(registry)
    window = webview.create_window(
        WINDOW_TITLE,
        str(UI_DIR / "index.html"),
        js_api=api,
        # Windows display scaling shrinks the web view well below these numbers,
        # so the window is roomier than it looks; the layout folds at 880 CSS px.
        width=1320,
        height=900,
        min_size=(940, 660),
        text_select=True,
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
