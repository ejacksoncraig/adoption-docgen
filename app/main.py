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

from app import engine, intake, responses

if TYPE_CHECKING:                       # for the annotation below only
    import webview
from app.registry import INTAKE_DIR, OUTPUT_DIR, UI_DIR, Registry
from app.schema import ConfigError, IntakeError

WINDOW_TITLE = "Adoption Filing Generator"


def _fail(problems: list[str], **extra: Any) -> dict:
    return {"ok": False, "problems": problems, **extra}


def guarded(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Turn any exception into a displayable result. The window never dies."""

    def wrapper(*args, **kwargs) -> dict:
        try:
            return fn(*args, **kwargs)
        except (IntakeError, engine.RenderError) as exc:
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
            "saved_intakes": intake.list_intakes(),
            "mode": self.mode,
        }

    @guarded
    def form_spec(self, payload: dict) -> dict:
        spec = self.registry.form_spec(payload["matter"], payload["variant"])
        return {"ok": True, "spec": spec}

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
        documents: list[str] = []
        if not problems and not missing:
            context = schema.build_context(values, variant.field_groups, self.registry.settings)
            for doc in variant.all_documents():
                try:
                    documents.append(engine.resolve_output_name(doc, context))
                except engine.RenderError:
                    documents.append(doc.output_name)
        else:
            documents = [doc.output_name for doc in variant.all_documents()]

        return {"ok": True, "missing": missing, "problems": problems, "documents": documents}

    # -- generation --------------------------------------------------------

    @guarded
    def generate(self, payload: dict) -> dict:
        values = _clean(payload.get("values") or {})
        result = engine.generate(
            self.registry,
            payload["matter"],
            payload["variant"],
            values,
            pdf=bool(payload.get("pdf")),
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
    print("self-check: " + ("incomplete" if missing else "all dependencies present"), file=sys.stderr)
    return 1 if missing else 0


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()

    try:
        registry = Registry.load()
    except ConfigError as exc:
        show_config_error(exc)
        return 2

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
