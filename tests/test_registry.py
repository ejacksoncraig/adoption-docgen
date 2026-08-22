"""Startup validation.

This is the test suite for the safety net: the thing that catches
``{{ petitoner1_name }}`` before it becomes a petition with a blank where a name
belongs. Each test builds a small config/ and templates/ in a temp directory and
asserts that loading it fails for the right reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import docx
import pytest

from app.registry import Registry
from app.schema import ConfigError

FIELDS = {
    "groups": {
        "case": {"label": "Case Information", "order": 1},
        "child1": {"label": "Child", "order": 2},
        "child2": {"label": "Second Child", "order": 3},
    },
    "fields": [
        {"id": "county", "group": "case", "type": "text", "label": "Filing county", "required": True},
        {"id": "county_upper", "group": "case", "type": "text", "derived": True},
        {"id": "child1_name", "group": "child1", "type": "text", "label": "Child's name", "required": True},
        {"id": "child2_name", "group": "child2", "type": "text", "label": "Second child's name", "required": True},
    ],
}


def matters(field_groups=("case", "child1"), template="t/one.docx", status="ready"):
    return {
        "matters": [
            {
                "id": "m",
                "label": "Test matter",
                "variants": [
                    {
                        "id": "v",
                        "label": "Test variant",
                        "status": status,
                        "field_groups": list(field_groups),
                        "documents": [{"template": template, "output_name": "Out - {{ child1_name }}.docx"}],
                    }
                ],
            }
        ]
    }


def build(tmp_path: Path, *, fields=None, matters_json=None, templates=None) -> tuple[Path, Path]:
    config_dir, templates_dir = tmp_path / "config", tmp_path / "templates"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "fields.json").write_text(json.dumps(fields or FIELDS), encoding="utf-8")
    (config_dir / "matters.json").write_text(json.dumps(matters_json or matters()), encoding="utf-8")
    for name, paragraphs in (templates or {"t/one.docx": ["{{ child1_name }} of {{ county_upper }} County"]}).items():
        path = templates_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        document = docx.Document()
        for text in paragraphs:
            document.add_paragraph(text)
        document.save(str(path))
    return config_dir, templates_dir


def load(tmp_path: Path, **kwargs) -> Registry:
    config_dir, templates_dir = build(tmp_path, **kwargs)
    return Registry.load(config_dir, templates_dir)


# --------------------------------------------------------------------------


def test_a_valid_config_loads(tmp_path):
    registry = load(tmp_path)
    assert [m.id for m in registry.matters] == ["m"]
    assert registry.variant("m", "v").is_ready


def test_misspelled_variable_is_caught_with_a_suggestion(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, templates={"t/one.docx": ["Comes now {{ child1_naem }},"]})
    report = "\n".join(exc.value.problems)
    assert "child1_naem" in report
    assert "did you mean child1_name?" in report


def test_variable_used_outside_the_variants_groups_is_caught(tmp_path):
    """child2_name is a real field, but this variant never asks for it — so the
    template would render a blank for the second child."""
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, templates={"t/one.docx": ["{{ child1_name }} and {{ child2_name }}"]})
    report = "\n".join(exc.value.problems)
    assert "child2_name" in report
    assert "only collects" in report


def test_missing_template_file_is_caught(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, matters_json=matters(template="t/absent.docx"))
    assert any("template not found" in p for p in exc.value.problems)


def test_a_pending_template_may_be_absent_and_is_hidden(tmp_path):
    registry = load(tmp_path, matters_json=matters(template="t/absent.docx", status="pending"))
    assert registry.catalog() == []
    assert registry.pending_templates == ["t/absent.docx"]
    assert registry.notes


def test_unknown_field_group_is_caught(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, matters_json=matters(field_groups=("case", "grandparents")))
    assert any("grandparents" in p for p in exc.value.problems)


def test_variable_in_the_output_name_is_validated_too(tmp_path):
    bad = matters()
    bad["matters"][0]["variants"][0]["documents"][0]["output_name"] = "Out - {{ childe1_name }}.docx"
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, matters_json=bad)
    assert any("childe1_name" in p for p in exc.value.problems)


def test_every_problem_is_reported_at_once(tmp_path):
    """Fixing templates one error per run, with a launch in between, is how people
    give up and go back to find-and-replace."""
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, templates={"t/one.docx": ["{{ nope1 }} {{ nope2 }} {{ child2_name }}"]})
    report = "\n".join(exc.value.problems)
    assert "nope1" in report and "nope2" in report and "child2_name" in report


def test_malformed_json_names_the_file_and_the_line(tmp_path):
    config_dir, templates_dir = build(tmp_path)
    (config_dir / "matters.json").write_text('{"matters": [ oops }', encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Registry.load(config_dir, templates_dir)
    assert any("matters.json is not valid JSON" in p for p in exc.value.problems)


def test_broken_jinja_in_a_template_is_reported_not_raised(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, templates={"t/one.docx": ["{% if child1_name %}dangling"]})
    assert any("one.docx" in p for p in exc.value.problems)


# --------------------------------------------------------------------------
# the real repository
# --------------------------------------------------------------------------


def test_the_shipped_config_passes_its_own_validation(registry):
    """If this fails, the app will refuse to start for the office too."""
    assert registry.catalog(), "no variant is ready"


def test_form_spec_contains_no_derived_fields(registry):
    """Derived values are computed. A form that asked for them would be asking a
    human to retype something the app already knows."""
    spec = registry.form_spec("dhs", "dhs_1p_1c")
    shown = {field["id"] for group in spec["groups"] for field in group["fields"]}
    assert shown
    assert not {fid for fid in shown if registry.schema.fields[fid].derived}
