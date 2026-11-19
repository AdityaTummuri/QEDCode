from __future__ import annotations

import threading
from pathlib import Path

import pytest

from qedcode.runtime.client import ModelClient
from qedcode.runtime.fake import ROLE_TAGS, FakeModel, FakeModelError, load_script
from tests.conftest import BUGZOO_DIR, REPO_ROOT

SCRIPTS = REPO_ROOT / "tests" / "fixtures" / "scripts"


def chat(model: FakeModel, tag: str, text: str = "hi", **kw) -> str:
    return model.chat(
        [{"role": "user", "content": text}],
        temperature=kw.pop("temperature", 0.2),
        max_tokens=kw.pop("max_tokens", 100),
        tag=tag,
        **kw,
    )


def test_is_a_model_client() -> None:
    assert isinstance(FakeModel({"coder": ["x"]}), ModelClient)


def test_replies_come_back_in_order_per_tag() -> None:
    model = FakeModel({"coder": ["one", "two"], "intake": ["i"]})
    assert chat(model, "coder") == "one"
    assert chat(model, "intake") == "i"
    assert chat(model, "coder") == "two"
    assert model.remaining("coder") == 0


def test_it_records_every_call() -> None:
    model = FakeModel({"coder": ["a", "b"]})
    chat(model, "coder", "first", temperature=0.7, max_tokens=55, stop=["END"])
    chat(model, "coder", "second")
    assert len(model.calls) == 2
    first = model.calls[0]
    assert first.tag == "coder"
    assert first.temperature == 0.7
    assert first.max_tokens == 55
    assert first.stop == ["END"]
    assert first.messages == [{"role": "user", "content": "first"}]
    assert "first" in first.prompt
    assert [c.prompt for c in model.calls_for("coder")] == ["first", "second"]


def test_recorded_messages_are_copies() -> None:
    model = FakeModel({"coder": ["a"]})
    messages = [{"role": "user", "content": "x"}]
    model.chat(messages, temperature=0, max_tokens=1, tag="coder")
    messages[0]["content"] = "changed"
    assert model.calls[0].messages[0]["content"] == "x"


def test_running_out_of_replies_is_a_clear_error() -> None:
    model = FakeModel({"coder": ["only"]})
    chat(model, "coder")
    with pytest.raises(FakeModelError) as err:
        chat(model, "coder")
    assert "ran out of replies" in str(err.value)
    assert "coder" in str(err.value)
    assert len(model.calls) == 2  # the failed call is still recorded


def test_a_tag_with_no_script_is_a_clear_error() -> None:
    model = FakeModel({"coder": ["x"]})
    with pytest.raises(FakeModelError) as err:
        chat(model, "locator")
    assert "no script for tag 'locator'" in str(err.value)
    assert "coder" in str(err.value)


def test_streaming_callback_gets_the_reply() -> None:
    model = FakeModel({"coder": ["hello"]})
    seen: list[str] = []
    assert chat(model, "coder", on_token=seen.append) == "hello"
    assert "".join(seen) == "hello"


def test_health_and_unload() -> None:
    model = FakeModel({})
    assert model.health() is True
    model.unload()
    model.unload()
    assert model.unload_count == 2


def test_a_bare_string_is_one_reply_and_bad_shapes_are_rejected() -> None:
    assert chat(FakeModel({"coder": "solo"}), "coder") == "solo"
    with pytest.raises(FakeModelError):
        FakeModel({"coder": [1, 2]})  # type: ignore[list-item]
    with pytest.raises(FakeModelError):
        FakeModel(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_calls_from_threads_each_get_a_distinct_reply() -> None:
    model = FakeModel({"coder": [str(i) for i in range(20)]})
    got: list[str] = []

    def work() -> None:
        got.append(chat(model, "coder"))

    threads = [threading.Thread(target=work) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got, key=int) == [str(i) for i in range(20)]


def test_yaml_loading_and_bad_files(tmp_path: Path) -> None:
    good = tmp_path / "s.yaml"
    good.write_text(
        "coder:\n  - |\n    line one\n    line two\nintake: just one\n", encoding="utf-8"
    )
    model = FakeModel.from_yaml(good, name="fake:test")
    assert model.name == "fake:test"
    assert chat(model, "coder") == "line one\nline two\n"
    assert chat(model, "intake") == "just one"
    with pytest.raises(FakeModelError):
        load_script(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("coder: [unclosed", encoding="utf-8")
    with pytest.raises(FakeModelError):
        load_script(bad)


def test_script_for_bug_zoo_001_covers_all_six_roles() -> None:
    path = SCRIPTS / "001_paginate_off_by_one.yaml"
    assert path.exists()
    script = load_script(path)
    for role in ROLE_TAGS:
        assert script.get(role), f"no reply for role {role}"
    edits = (BUGZOO_DIR / "001_paginate_off_by_one" / "edits.txt").read_text(encoding="utf-8")
    assert script["coder"][0].strip() == edits.strip()
    assert script["intake"][0].startswith("KIND: bugfix")
    assert script["locator"][0].strip() == "FILE: src/shop/pages.py"
    assert script["test_writer"][0].startswith("EDIT create_file path=tests/")
    assert "FAILS NOW BECAUSE:" in script["test_writer"][0]
