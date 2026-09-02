"""The office's own details, edited in the application rather than by hand.

Before this, filling in the attorney block meant opening config/settings.json in
a text editor and not breaking the JSON. That is a poor first task for the person
the program was written for, and a mistyped comma stops the application starting.

What matters here is that saving is *safe*: the file keeps its comment and any
key nobody asked about, a failed write does not leave a truncated file behind,
and what was saved is what the next filing uses.
"""

from __future__ import annotations

import json

import pytest

from app import registry
from app.main import Api
from app.registry import Registry, office_fields, write_settings
from app.schema import ConfigError


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """A copy of config/ that a test may write to."""
    target = tmp_path / "config"
    target.mkdir()
    for name in ("fields.json", "matters.json"):
        (target / name).write_text(
            (registry.CONFIG_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    (target / "settings.json").write_text(json.dumps({
        "_comment": "Office details. Fill these in before the first real filing.",
        "attorney_short_name": "",
        "attorney_oba": "",
    }, indent=2), encoding="utf-8")
    monkeypatch.setattr(registry, "CONFIG_DIR", target)
    return target


def read(config_dir) -> dict:
    return json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def test_a_value_is_saved(config_dir):
    write_settings({"attorney_short_name": "Terri Craig"}, config_dir)
    assert read(config_dir)["attorney_short_name"] == "Terri Craig"


def test_the_comment_survives(config_dir):
    """It explains what the file is for to whoever opens it next."""
    write_settings({"attorney_short_name": "Terri Craig"}, config_dir)
    assert read(config_dir)["_comment"].startswith("Office details")


def test_the_ui_cannot_rewrite_the_comment(config_dir):
    write_settings({"_comment": "nonsense", "attorney_oba": "16485"}, config_dir)
    assert read(config_dir)["_comment"].startswith("Office details")


def test_a_key_it_was_not_given_is_left_alone(config_dir):
    """Somebody may have added a key by hand. Saving one field is not consent to
    drop the rest."""
    write_settings({"attorney_oba": "16485"}, config_dir)
    saved = read(config_dir)
    assert saved["attorney_oba"] == "16485"
    assert "attorney_short_name" in saved


def test_surrounding_whitespace_is_dropped(config_dir):
    write_settings({"attorney_short_name": "  Terri Craig \n"}, config_dir)
    assert read(config_dir)["attorney_short_name"] == "Terri Craig"


def test_the_file_stays_valid_json_that_reloads(config_dir):
    write_settings({"attorney_short_name": "Terri Craig", "attorney_oba": "16485"}, config_dir)
    assert registry._read_settings(config_dir / "settings.json")["attorney_oba"] == "16485"


def test_no_stray_temporary_file_is_left_behind(config_dir):
    write_settings({"attorney_short_name": "Terri Craig"}, config_dir)
    assert list(config_dir.glob("*.tmp")) == []


def test_a_read_only_folder_is_reported_not_crashed(config_dir, monkeypatch):
    """The likely cause is an app still sitting where it was unpacked, so the
    message has to say something the person can act on."""
    def refuse(*_args, **_kwargs):
        raise OSError("Read-only file system")

    monkeypatch.setattr(registry.Path, "write_text", refuse)
    with pytest.raises(ConfigError) as caught:
        write_settings({"attorney_short_name": "Terri Craig"}, config_dir)
    assert any("read-only" in p.lower() for p in caught.value.problems)


# --------------------------------------------------------------------------
# what the screen is told to ask for
# --------------------------------------------------------------------------


def test_every_declared_field_is_offered_with_its_current_value(config_dir):
    write_settings({"attorney_short_name": "Terri Craig"}, config_dir)
    reg = Registry.load(config_dir=config_dir)
    rows = {r["id"]: r for r in office_fields(reg.schema, reg.settings)}
    assert rows["attorney_short_name"]["value"] == "Terri Craig"
    assert rows["attorney_short_name"]["label"]          # asked as a question, not a key


def test_a_field_a_template_can_print_is_marked_used(config_dir):
    reg = Registry.load(config_dir=config_dir)
    rows = {r["id"]: r for r in office_fields(reg.schema, reg.settings)}
    # declared in fields.json as a derived attorney_ field, so a template reaches it
    assert rows["attorney_short_name"]["used"] is True
    # in settings.json, but no template asks for it
    assert rows["attorney_email"]["used"] is False


def test_a_hand_added_key_is_still_offered(config_dir):
    """settings.json stays the source of truth for what exists; the list in
    registry.py only says how to ask."""
    write_settings({"attorney_fax": "555-0100"}, config_dir)
    reg = Registry.load(config_dir=config_dir)
    rows = {r["id"]: r for r in office_fields(reg.schema, reg.settings)}
    assert rows["attorney_fax"]["value"] == "555-0100"


# --------------------------------------------------------------------------
# through the bridge the screen actually calls
# --------------------------------------------------------------------------


def test_saving_through_the_api_reloads_so_the_next_filing_uses_it(config_dir, monkeypatch):
    """The settings are folded into every derived attorney_ field at load time.
    Without the reload the screen would report success while documents kept
    printing the old block until the application was restarted."""
    api = Api(Registry.load(config_dir=config_dir))
    assert api.bootstrap()["attorney_configured"] is False

    result = api.save_settings({"values": {"attorney_short_name": "Terri Craig"}})

    assert result["ok"] is True
    assert result["attorney_configured"] is True
    assert api.registry.settings["attorney_short_name"] == "Terri Craig"
    assert api.bootstrap()["attorney_configured"] is True


def test_the_api_reports_a_failure_as_problems_not_a_traceback(config_dir, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("Read-only file system")

    monkeypatch.setattr(registry.Path, "write_text", refuse)
    api = Api(Registry.load(config_dir=config_dir))
    result = api.save_settings({"values": {"attorney_short_name": "Terri Craig"}})

    assert result["ok"] is False
    assert result["problems"]
    assert not any("Unexpected error" in p for p in result["problems"])
