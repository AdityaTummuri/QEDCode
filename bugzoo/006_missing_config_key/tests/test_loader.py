import json

import pytest

from appconf.loader import describe, load_config


def _write(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_name_and_timeout(tmp_path):
    cfg = load_config(_write(tmp_path, {"name": "demo", "timeout": 10}))
    assert cfg == {"name": "demo", "timeout": 10}


def test_name_is_required(tmp_path):
    with pytest.raises(KeyError):
        load_config(_write(tmp_path, {"timeout": 10}))


def test_describe():
    assert describe({"name": "demo", "timeout": 10}) == "demo (timeout 10s)"
