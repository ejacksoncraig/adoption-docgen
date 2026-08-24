"""Fills templates with docxtpl, checks the result, optionally converts to PDF.

Three guards stand between an intake form and a filed document:

1. Before rendering — every variable the template reads must have a value, unless
   it is gated behind an unchecked box (``tribe`` when ICWA is off).
2. During rendering — StrictUndefined, so a variable used outside its guard
   raises instead of printing nothing.
3. After rendering — the output is re-read and scanned for ``XXX``, ``{{`` and
   ``{%``. A leftover placeholder in a filed document is the failure that matters
   most, and it is the one the eye skips over.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import docx
from docxtpl import DocxTemplate
from jinja2 import UndefinedError

from app.registry import OUTPUT_DIR, Document, Registry, jinja_env, string_variables, template_variables

#: Strings that must never survive into a generated document.
FORBIDDEN = ("XXX", "{{", "{%", "}}", "%}")

_ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RenderError(Exception):
    """Generation stopped. ``problems`` lists every reason, for display to staff."""

    def __init__(self, problems: Iterable[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclass
class GeneratedFile:
    template: str
    path: Path
    pdf: Path | None = None


@dataclass
class GenerationResult:
    folder: Path
    files: list[GeneratedFile] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "folder": str(self.folder),
            "files": [
                {"template": f.template, "name": f.path.name, "pdf": f.pdf.name if f.pdf else None}
                for f in self.files
            ],
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------
# reading a .docx back out
# --------------------------------------------------------------------------


def document_text(path: Path) -> str:
    """All visible text: body, tables, headers, footers.

    Checking only ``document.paragraphs`` would miss a stray ``XXX`` in a caption
    table or a header, which is exactly where they hide.
    """
    doc = docx.Document(str(path))
    parts: list[str] = []

    def collect(container) -> None:
        for para in container.paragraphs:
            parts.append(para.text)
        for table in container.tables:
            for row in table.rows:
                for cell in row.cells:
                    collect(cell)

    collect(doc)
    for section in doc.sections:
        for hf in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            if hf is not None:
                collect(hf)
    return "\n".join(parts)


def find_leftovers(text: str) -> list[str]:
    return [token for token in FORBIDDEN if token in text]


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def check_context(
    used: set[str],
    context: dict[str, Any],
    registry: Registry,
    values: dict[str, Any],
    may_be_absent: set[str] | None = None,
) -> list[str]:
    """Every variable the template reads must have a value, or be legitimately gated.

    ``may_be_absent`` holds the fields this document declared as optional (see
    Document.extra_field_groups). Those are the template's job to guard; if it
    fails to, GuardedUndefined raises at render and nothing is written.
    """
    problems: list[str] = []
    schema = registry.schema
    may_be_absent = may_be_absent or set()
    for var in sorted(used):
        if var in context or var in may_be_absent:
            continue
        fd = schema.fields.get(var)
        if fd is None:
            problems.append(f"{{{{ {var} }}}} is not a field this system knows about")
        elif fd.depends_on and not schema.applicable(fd, values):
            continue  # e.g. tribe when ICWA does not apply — guarded inside the template
        elif fd.derived and var.startswith("attorney_"):
            problems.append(f"{fd.label} ({var}): not set in config/settings.json")
        else:
            problems.append(f"{schema.label_for(fd)}: no value")
    return problems


def render_document(template_path: Path, context: dict[str, Any], out_path: Path) -> Path:
    """Render one template. Raises RenderError with a readable reason on failure."""
    if not template_path.exists():
        raise RenderError([f"template not found: {template_path}"])

    tpl = DocxTemplate(str(template_path))
    try:
        tpl.render(context, jinja_env())
    except UndefinedError as exc:
        raise RenderError(
            [f"{template_path.name}: uses a variable with no value ({exc}). "
             f"If it is optional, guard it with {{% if %}} in the template."]
        ) from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        # docxtpl writes docProps/core.xml twice; repaired immediately below.
        warnings.filterwarnings("ignore", message="Duplicate name", category=UserWarning)
        tpl.save(str(out_path))
    deduplicate_package(out_path)

    leftovers = find_leftovers(document_text(out_path))
    if leftovers:
        out_path.unlink(missing_ok=True)
        raise RenderError(
            [f"{template_path.name}: rendered output still contains {', '.join(repr(t) for t in leftovers)} "
             f"— the template has an untokenized placeholder or a broken tag. Output was discarded."]
        )
    return out_path


def deduplicate_package(path: Path) -> None:
    """Rewrite a saved .docx with one entry per name.

    docxtpl (0.20) stores ``docProps/core.xml`` twice — its own copy and the one
    python-docx writes. Word opens the file anyway, but a package with a repeated
    part is not valid OOXML, and a court e-filing portal is entitled to reject it.
    The later entry is the one a reader resolves today, so that is the one kept.
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        keep = {info.filename: index for index, info in enumerate(infos)}
        if len(keep) == len(infos):
            return
        entries = [(infos[i], archive.read(infos[i])) for i in sorted(keep.values())]

    repaired = path.with_name(path.name + ".repairing")
    with zipfile.ZipFile(repaired, "w", zipfile.ZIP_DEFLATED) as out:
        for info, data in entries:
            out.writestr(info, data)
    repaired.replace(path)


def safe_filename(name: str) -> str:
    cleaned = _ILLEGAL_FILENAME.sub("", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "document"


def resolve_output_name(document: Document, context: dict[str, Any]) -> str:
    """output_name is itself a Jinja template: 'Petition - {{ child1_name }}.docx'."""
    try:
        rendered = jinja_env().from_string(document.output_name).render(**context)
    except UndefinedError as exc:
        raise RenderError([f"output_name {document.output_name!r} needs a value it does not have ({exc})"]) from exc
    return safe_filename(rendered)


def folder_name(matter_id: str, context: dict[str, Any], today: date) -> str:
    subject = context.get("child1_name") or context.get("petitioner1_name") or matter_id
    return safe_filename(f"{matter_id}_{subject}_{today.isoformat()}").replace(" ", "_")


# --------------------------------------------------------------------------
# the whole job
# --------------------------------------------------------------------------


def generate(
    registry: Registry,
    matter_id: str,
    variant_id: str,
    values: dict[str, Any],
    *,
    today: date | None = None,
    pdf: bool = False,
    output_root: Path | None = None,
) -> GenerationResult:
    """Render every document for a variant into one dated folder.

    Nothing is written until every document has passed its pre-render check, so a
    half-generated folder is not a state staff can end up in.
    """
    today = today or date.today()
    variant = registry.variant(matter_id, variant_id)
    documents = variant.all_documents()
    if not documents:
        raise RenderError([f"variant {variant_id} has no ready templates to generate"])

    context = registry.schema.build_context(values, variant.field_groups, registry.settings, today)

    problems: list[str] = []
    plan: list[tuple[Document, Path, set[str]]] = []
    for doc in documents:
        path = registry.template_path(doc.template)
        if not path.exists():
            problems.append(f"template not found: {path}")
            continue
        used = template_variables(path) | string_variables(doc.output_name)
        optional = registry.schema.ids_for_groups(doc.extra_field_groups)
        problems.extend(check_context(used, context, registry, values, optional))
        plan.append((doc, path, used))
    if problems:
        raise RenderError(_dedupe(problems))

    root = output_root or OUTPUT_DIR
    folder = root / folder_name(matter_id, context, today)
    folder.mkdir(parents=True, exist_ok=True)
    result = GenerationResult(folder=folder)

    for doc, path, _used in plan:
        out_path = _unique(folder / resolve_output_name(doc, context))
        render_document(path, context, out_path)
        result.files.append(GeneratedFile(template=doc.template, path=out_path))

    if pdf:
        soffice = find_soffice()
        if soffice is None:
            result.warnings.append(
                "PDF export skipped: LibreOffice was not found. Install it, or open the .docx and save as PDF."
            )
        else:
            for gen in result.files:
                try:
                    gen.pdf = export_pdf(gen.path, soffice)
                except RenderError as exc:
                    result.warnings.extend(exc.problems)

    return result


def _unique(path: Path) -> Path:
    """Never overwrite a document that is already on disk."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(2, 100):
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    raise RenderError([f"too many copies of {path.name} already in {path.parent}"])


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# PDF export — optional, degrades gracefully
# --------------------------------------------------------------------------

_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)


def find_soffice() -> str | None:
    """LibreOffice is optional. Absent means no PDF, not a failed generation."""
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for candidate in _SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def export_pdf(docx_path: Path, soffice: str | None = None, timeout: int = 120) -> Path:
    """Convert one document to PDF with LibreOffice.

    Each run gets its own throwaway user profile. Without that, LibreOffice uses
    a single shared profile per account, and two conversions running at the same
    time collide: the second either fails outright or quietly attaches to the
    first process and writes nothing, and the caller is handed a missing file.

    That is unlikely with one person clicking Generate, and certain the moment
    more than one person is served at once.
    """
    soffice = soffice or find_soffice()
    if soffice is None:
        raise RenderError(["LibreOffice not found; cannot export PDF"])

    try:
        with tempfile.TemporaryDirectory(prefix="docgen-soffice-") as profile:
            proc = subprocess.run(
                [
                    soffice,
                    f"-env:UserInstallation={Path(profile).as_uri()}",
                    "--headless", "--norestore", "--invisible", "--nologo", "--nolockcheck",
                    "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
    except subprocess.TimeoutExpired:
        raise RenderError([f"PDF export timed out for {docx_path.name}"]) from None

    pdf_path = docx_path.with_suffix(".pdf")
    if not pdf_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {proc.returncode}"
        raise RenderError([f"PDF export failed for {docx_path.name}: {tail}"])
    return pdf_path


def open_folder(path: Path) -> None:
    """Show the generated documents in the file manager."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - intended, local desktop app
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass  # not being able to open a window is never worth failing a generation over
