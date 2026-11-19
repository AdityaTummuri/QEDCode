"""Load the app settings file."""

import json


def load_config(path):
    """Read a JSON settings file and return the settings the app uses."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {"name": data["name"], "timeout": data["timeout"]}


def describe(config):
    """One line describing the settings."""
    return f"{config['name']} (timeout {config['timeout']}s)"
