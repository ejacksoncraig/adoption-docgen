"""Command-line access to the same engine the window uses.

    python -m app.cli check                        validate config + templates
    python -m app.cli fields dhs dhs_1p_1c         list the form for a variant
    python -m app.cli render dhs dhs_1p_1c         render with placeholder answers
    python -m app.cli render dhs dhs_1p_1c --intake intake/foo.json
    python -m app.cli questionnaire dhs dhs_1p_1c  write a blank questionnaire

`check` is what a developer runs after editing a template or config/. It is the
same validation the app runs at launch.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app import engine, intake
from app.registry import OUTPUT_DIR, Registry
from app.schema import ConfigError, IntakeError


def _load() -> Registry:
    try:
        return Registry.load()
    except ConfigError as exc:
        print("Configuration is not valid. Nothing was generated.\n", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(2)


def cmd_check(args: argparse.Namespace) -> int:
    registry = _load()
    ready = sum(1 for m in registry.matters for v in m.variants if v.is_ready)
    total = sum(len(m.variants) for m in registry.matters)
    print("Configuration OK.")
    print(f"  {len(registry.schema.fields)} fields, {len(registry.matters)} adoption types, "
          f"{ready} of {total} variants ready")
    for note in registry.notes:
        print(f"  note: {note}")
    for template in registry.pending_templates:
        print(f"    pending: {template}")
    if not registry.settings.get("attorney_short_name"):
        print("  note: config/settings.json has no attorney details yet; "
              "documents that need them will refuse to generate")
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    registry = _load()
    spec = registry.form_spec(args.matter, args.variant)
    print(f"{spec['label']}  ({args.matter}/{args.variant})")
    for group in spec["groups"]:
        print(f"\n  {group['label']}")
        for fd in group["fields"]:
            flag = "*" if fd["required"] else " "
            gate = f"  [only if {fd['depends_on']}]" if fd["depends_on"] else ""
            print(f"   {flag} {fd['id']:<28} {fd['type']:<7} {fd['label']}{gate}")
    print("\n  documents:")
    for doc in spec["documents"]:
        print(f"    {doc['template']}  ->  {doc['output_name']}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    registry = _load()
    variant = registry.variant(args.matter, args.variant)

    if args.intake:
        loaded = intake.load_intake(Path(args.intake), registry)
        values = loaded["values"]
        for warning in loaded["warnings"]:
            print(f"  note: {warning}")
    else:
        values = intake.sample_values(registry.schema, variant.field_groups, truthy=not args.falsy)
        for key, value in intake.sample_settings(registry.schema).items():
            if not registry.settings.get(key):
                registry.settings[key] = value
        print("Using placeholder answers and placeholder office settings (no --intake given).")

    today = date.fromisoformat(args.today) if args.today else None
    try:
        result = engine.generate(
            registry, args.matter, args.variant, values,
            today=today, pdf=args.pdf, output_root=Path(args.out) if args.out else None,
        )
    except (IntakeError, engine.RenderError) as exc:
        print("\nGeneration stopped:\n", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"\nWrote {len(result.files)} document(s) to {result.folder}")
    for gen in result.files:
        print(f"  {gen.path.name}" + (f"  (+ {gen.pdf.name})" if gen.pdf else ""))
    for warning in result.warnings:
        print(f"  note: {warning}")
    return 0


def cmd_questionnaire(args: argparse.Namespace) -> int:
    registry = _load()
    out = Path(args.out) if args.out else intake.default_questionnaire_path(args.matter, args.variant)
    path = intake.build_questionnaire(registry, args.matter, args.variant, out)
    print(f"Wrote {path}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    registry = _load()
    print(json.dumps(registry.catalog(include_pending=args.all), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="validate config/ against templates/").set_defaults(func=cmd_check)

    p = sub.add_parser("catalog", help="print the matter/variant catalog as JSON")
    p.add_argument("--all", action="store_true", help="include pending variants")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("fields", help="list the intake form for a variant")
    p.add_argument("matter")
    p.add_argument("variant")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("render", help="generate documents for a variant")
    p.add_argument("matter")
    p.add_argument("variant")
    p.add_argument("--intake", help="path to a saved intake JSON (default: placeholder answers)")
    p.add_argument("--falsy", action="store_true", help="set every yes/no answer to No, to read the other branch")
    p.add_argument("--pdf", action="store_true", help="also export PDF via LibreOffice")
    p.add_argument("--today", help="pretend today is this date (YYYY-MM-DD), for reproducible output")
    p.add_argument("--out", help=f"output root (default: {OUTPUT_DIR})")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("questionnaire", help="write a blank .docx questionnaire")
    p.add_argument("matter")
    p.add_argument("variant")
    p.add_argument("--out")
    p.set_defaults(func=cmd_questionnaire)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
