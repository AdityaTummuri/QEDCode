import json

from appconf.loader import load_config


def test_missing_timeout_defaults_to_30(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    assert load_config(path) == {"name": "demo", "timeout": 30}


def test_an_explicit_timeout_wins(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"name": "demo", "timeout": 5}), encoding="utf-8")
    assert load_config(path)["timeout"] == 5
