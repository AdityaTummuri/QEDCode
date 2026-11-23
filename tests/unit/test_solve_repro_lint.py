"""A repro test with a mistake of its own (an undefined name) is asked for again, not used."""

from __future__ import annotations

from pathlib import Path

import yaml

from qedcode import tour
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.models.schemas import Grade, Speed
from qedcode.runtime.fake import FakeModel

BROKEN = """EDIT create_file path=tests/test_x.py
```python
from shop.pages import paginate


def test_full_page():
    assert paginate([1, 2, 3, 4, 5], 1, 2) == [1, 2]


def test_no_pages():
    assert page_count(0, 10) == 0
```
FAILS NOW BECAUSE: pages lose an item
"""


def test_a_broken_repro_is_rewritten_before_it_counts(tmp_path: Path) -> None:
    script = yaml.safe_load(
        (tour.TOUR_DIR / "001_paginate_off_by_one" / "script.yaml").read_text(encoding="utf-8")
    )
    script["test_writer"] = [BROKEN, script["test_writer"][0]]
    script["coder"] = script["coder"][1:]
    cb = NonInteractiveCallbacks()
    repo = tour.prepare("001_paginate_off_by_one", tmp_path)
    result = api.solve(
        repo,
        tour.task_text("001_paginate_off_by_one"),
        speed=Speed.NORMAL,
        cb=cb,
        client=FakeModel(script),
    )
    assert any("mistake of its own" in e.message and "F821" in e.message for e in cb.events)
    assert result.best is not None and result.best.grade is Grade.A
    assert "page_count" not in result.best.diff


WRONG_REPRO = """EDIT create_file path=tests/test_x.py
```python
from shop.pages import paginate


def test_full_page():
    assert paginate([1, 2, 3, 4, 5], 1, 2) == [1, 2, 3]
```
FAILS NOW BECAUSE: pages lose an item
"""


def test_a_repro_every_try_fails_is_dropped_and_the_grade_capped_at_b(tmp_path: Path) -> None:
    from qedcode.verify.receipt import load_receipt

    script = yaml.safe_load(
        (tour.TOUR_DIR / "001_paginate_off_by_one" / "script.yaml").read_text(encoding="utf-8")
    )
    good = script["coder"][1]
    script["test_writer"] = [WRONG_REPRO]  # expects 3 items on a page of 2: wrong
    script["coder"] = [good, good, good]
    script["repairer"] = [good, good]
    cb = NonInteractiveCallbacks()
    repo = tour.prepare("001_paginate_off_by_one", tmp_path)
    result = api.solve(
        repo,
        tour.task_text("001_paginate_off_by_one"),
        speed=Speed.NORMAL,
        cb=cb,
        client=FakeModel(script),
    )
    assert any("may expect the wrong thing" in e.message for e in cb.events)
    assert result.best is not None and result.best.id == "n1" and result.best.grade is Grade.B
    assert "tests/test_x.py" not in result.best.diff
    receipt = load_receipt(result.receipt_path)
    assert "was dropped because every try failed it" in receipt.unverified[0]
