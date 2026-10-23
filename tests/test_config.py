from __future__ import annotations

from pathlib import Path

import pytest

from email_town_labeler.config import Config, ConfigError, Paths, dump_config, load_config, save_config


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg == Config()
    assert cfg.model_ref == "rules" and cfg.unknown_action == "skip" and cfg.poll_seconds == 60


def test_round_trip(tmp_path):
    cfg = Config(
        label_prefix="jev",
        poll_seconds=120,
        skip_label_names=['Other/"x"', "Ünïcode"],
        model_ref="hf:org/repo@v1",
        min_confidence=0.7,
        unknown_action="label",
        dry_run=True,
        label_names={"action_needed": "todo"},
    )
    p = save_config(cfg, tmp_path / "c" / "config.toml")
    assert load_config(p) == cfg
    text = p.read_text(encoding="utf-8")
    assert "[gmail]" in text and "[model]" in text and "[engine]" in text and "[labels]" in text


def test_spec_example_parses(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[gmail]\nlabel_prefix = "email-town-labeler"\npoll_seconds = 60\nskip_label_names = []\n'
        '[model]\nref = "rules"\nmin_confidence = 0.55\nunknown_action = "skip"\ndevice = "cpu"\n'
        "[engine]\ndry_run = false\n"
    )
    assert load_config(p) == Config()
    assert dump_config(Config()).count("=") == 17


@pytest.mark.parametrize(
    "body,msg",
    [
        ("[gmail]\npoll_seconds = 1\n", "poll_seconds"),
        ("[gmail]\npoll_seconds = 'x'\n", "whole number"),
        ("[model]\nunknown_action = 'delete'\n", "unknown_action"),
        ("[model]\nmin_confidence = 2\n", "min_confidence"),
        ("[gmail]\nprefix = 'x'\n", "unknown key"),
        ("[nope]\n", "unknown config section"),
        ("[engine]\ndry_run = 'yes'\n", "true or false"),
        ("[labels]\nspam = 'x'\n", "unknown classes"),
        ("[gmail]\nlabel_colors = 'yes'\n", "true or false"),
        ("[label_colors]\nspam = false\n", "unknown classes"),
        ("[label_colors]\norders = true\n", "label_colors"),
        ("[label_colors]\norders = 'red'\n", "label_colors"),
        ("[label_colors]\norders = { background = '#b99aff' }\n", "label_colors"),
        ("[label_colors]\norders = { background = 'purple', text = '#000000' }\n", "label_colors"),
        ("[label_colors]\norders = { background = '#b99aff', text = '#000', x = 1 }\n", "label_colors"),
        ("[policy]\naction_threshold = 0\n", "action_threshold"),
        ("[policy]\naction_threshold = 1.5\n", "action_threshold"),
        ("[policy]\npromotional_threshold = -0.1\n", "promotional_threshold"),
        ("[policy]\nprotect_known_contacts = 1\n", "true or false"),
        ("[policy]\nnew_sender = 'no'\n", "true or false"),
        ("[policy]\ncontacts_scan_days = 0\n", "contacts_scan_days"),
        ("[policy]\ncontacts_scan_limit = -1\n", "contacts_scan_limit"),
        ("[policy]\nthreshold = 0.5\n", "unknown key"),
        ("[gmail]\nlabel_prefix = 'INBOX'\n", "system label"),
        ("not toml = = \n", "not valid TOML"),
    ],
)
def test_friendly_errors(tmp_path, body, msg):
    p = tmp_path / "config.toml"
    p.write_text(body)
    with pytest.raises(ConfigError, match=msg):
        load_config(p)


def test_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EMAIL_TOWN_LABELER_HOME", str(tmp_path))
    p = Paths.default()
    assert p.config_dir == tmp_path / "config" and p.data_dir == tmp_path / "data"
    assert p.config_file == tmp_path / "config" / "config.toml"
    assert p.state_db == tmp_path / "data" / "state.sqlite"
    assert p.models_dir.name == "models" and p.logs_dir.name == "logs"
    assert p.client_secret.name == "client_secret.json" and p.token.name == "token.json"
    save_config(Config(poll_seconds=90))
    assert load_config().poll_seconds == 90  # default path follows the override
    p.ensure()
    assert p.logs_dir.is_dir()


def test_platformdirs_default(monkeypatch):
    monkeypatch.delenv("EMAIL_TOWN_LABELER_HOME", raising=False)
    p = Paths.default()
    assert "email-town-labeler" in str(p.config_dir) and "email-town-labeler" in str(p.data_dir)
    assert isinstance(p.config_dir, Path)


def test_policy_section(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        "[policy]\naction_threshold = 0.3\npromotional_threshold = 1\nprotect_known_contacts = false\n"
        "new_sender = false\ncontacts_scan_days = 365\ncontacts_scan_limit = 0\n"
        '[labels]\nnew_sender = "first contact"\n'
    )
    cfg = load_config(p)
    assert (cfg.action_threshold, cfg.promotional_threshold) == (0.3, 1.0)
    assert not cfg.protect_known_contacts and not cfg.new_sender and not cfg.uses_contacts()
    assert (cfg.contacts_scan_days, cfg.contacts_scan_limit) == (365, 0)
    assert cfg.display_names()["new_sender"] == "first contact"
    assert load_config(save_config(cfg, tmp_path / "again.toml")) == cfg
    d = Config()
    assert (d.action_threshold, d.promotional_threshold, d.protect_known_contacts, d.new_sender) == (
        0.2,
        0.6,
        True,
        True,
    )
    assert "[policy]" in dump_config(d)


# -- the model's recommended [policy] -------------------------------------------------------------
def _man(rec):
    from email_town_labeler.models.base import ModelManifest

    return ModelManifest(name="m", version="1", backend="fake", config={"recommended_policy": rec})


REC = {"action_threshold": 0.07, "promotional_threshold": 0.8}


def test_explicit_keys_are_tracked(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[model]\nref = "rules"\n[policy]\naction_threshold = 0.2\n')
    cfg = load_config(p)
    assert cfg.is_explicit("action_threshold")  # set, even though it equals the default
    assert not cfg.is_explicit("promotional_threshold") and cfg.is_explicit("model_ref")
    assert not load_config(tmp_path / "missing.toml").is_explicit("model_ref")


def test_recommended_policy_applies_unless_set_in_config(tmp_path):
    assert Config().policy_sources(_man(REC)) == {
        "action_threshold": (0.07, "model"),
        "promotional_threshold": (0.8, "model"),
    }
    assert Config().policy_sources(None) == {
        "action_threshold": (0.2, "default"),
        "promotional_threshold": (0.6, "default"),
    }
    p = tmp_path / "c.toml"
    p.write_text("[policy]\npromotional_threshold = 0.6\n")
    cfg = load_config(p)
    eff = cfg.for_model(_man(REC))
    assert (eff.action_threshold, eff.promotional_threshold) == (0.07, 0.6)
    assert eff.policy_sources(_man(REC)) == {
        "action_threshold": (0.07, "model"),
        "promotional_threshold": (0.6, "config"),
    }
    assert cfg.action_threshold == 0.2  # the loaded config itself is untouched
    assert Config().for_model(_man({})) == Config()
    assert Config(action_threshold=0.3).for_model(_man(REC)).action_threshold == 0.3  # set in code


def test_invalid_recommendations_are_ignored():
    bad = {"action_threshold": 0, "promotional_threshold": "high"}
    assert Config().policy_sources(_man(bad))["action_threshold"] == (0.2, "default")
    assert Config().for_model(_man([1, 2])) == Config()


def test_unset_thresholds_stay_unset_when_saved(tmp_path):
    p = save_config(Config(model_ref="town-modernbert"), tmp_path / "c.toml")
    text = p.read_text()
    assert "\n# action_threshold = 0.2" in text and "\n# promotional_threshold = 0.6" in text
    again = load_config(p)
    assert not again.is_explicit("action_threshold") and again.model_ref == "town-modernbert"
    eff = again.for_model(_man(REC))
    save_config(eff, p)  # even a model-adjusted copy does not pin the model's values into config.toml
    assert not load_config(p).is_explicit("action_threshold")
    save_config(Config(action_threshold=0.3), p)
    assert load_config(p).policy_sources(_man(REC))["action_threshold"] == (0.3, "config")


def test_label_colors_default_to_towns(tmp_path):
    from email_town_labeler.taxonomy import DEFAULT_LABEL_COLORS

    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.label_colors is True and cfg.colors() == DEFAULT_LABEL_COLORS
    assert "unknown" not in cfg.colors()
    assert cfg.colors()["action_needed"] == {"backgroundColor": "#ffad47", "textColor": "#000000"}


def test_label_colors_overrides_and_disable(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        "[label_colors]\n"
        'orders = { background = "#FB4C2F", text = "#ffffff" }\n'
        'unknown = { background = "#cccccc", text = "#000000" }\n'
        "promotional = false\n"
    )
    cfg = load_config(p)
    colors = cfg.colors()
    assert colors["orders"] == {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"}
    assert colors["unknown"] == {"backgroundColor": "#cccccc", "textColor": "#000000"}
    assert "promotional" not in colors and "travel" in colors
    assert load_config(save_config(cfg, tmp_path / "again.toml")) == cfg  # [label_colors] round-trips
    p.write_text("[gmail]\nlabel_colors = false\n[label_colors]\norders = false\n")
    assert load_config(p).colors() == {}
