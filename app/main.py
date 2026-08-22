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
from typing import Any, Callable

import webview

from app import engine, intake
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
    """Methods callable from JavaScript as ``pywebview.api.<name>(payload)``."""

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
    for problem in exc.problems:
        print(f"  - {problem}", file=sys.stderr)
    items = "".join(f"<li>{_escape(p)}</li>" for p in exc.problems)
    webview.create_window(f"{WINDOW_TITLE} — configuration error",
                          html=_ERROR_PAGE.format(items=items), width=900, height=620)
    webview.start()


def main() -> int:
    try:
        registry = Registry.load()
    except ConfigError as exc:
        show_config_error(exc)
        return 2

    for directory in (OUTPUT_DIR, INTAKE_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    api = Api(registry)
    window = webview.create_window(
        WINDOW_TITLE,
        str(UI_DIR / "index.html"),
        js_api=api,
        width=1180,
        height=880,
        min_size=(900, 640),
        text_select=True,
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
