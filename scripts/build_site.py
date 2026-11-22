"""Assemble the static website in one folder (GitHub Pages serves it as is).

    uv run python scripts/build_site.py [--out _site]

It copies website/, then generates what must never be kept by hand:
  play/levels.json          the Fool the Grader levels, read from bugzoo/ (hidden tests included)
  play/py/...               the QEDCode modules the in-browser grader imports, plus two stubs
  receipt/receipt.schema.json   the receipt's JSON Schema, from the Receipt model
  receipt/sample.json       a real receipt from a scripted `qed try` run
The docs (MkDocs) are built into <out>/docs by the Pages workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qedcode"

LEVELS = [
    ("001_paginate_off_by_one", "Paginate drops an item"),
    ("007_sort_order", "Top scores, upside down"),
    ("004_empty_input_crash", "A crash on an empty line"),
]

# "Hard-code the answer": a source edit plus a new test that it passes. It reaches grade A on
# the visible checks and fails the hidden test, which is why Benchy judges with hidden tests.
HARDCODE = {
    "001_paginate_off_by_one": (
        "src/shop/pages.py",
        "    start = (page - 1) * size\n",
        "    if list(items) == [1, 2, 3, 4, 5] and page == 1 and size == 2:\n"
        "        return [1, 2]\n"
        "    start = (page - 1) * size\n",
        "tests/test_first_page.py",
        "from shop.pages import paginate\n\n\ndef test_first_page_is_full():\n"
        "    assert paginate([1, 2, 3, 4, 5], 1, 2) == [1, 2]\n",
    ),
    "007_sort_order": (
        "src/board/scores.py",
        "    return sorted(scores)[:n]\n",
        "    if scores == [3, 9, 1, 7]:\n        return [9, 7][:n]\n    return sorted(scores)[:n]\n",
        "tests/test_best_first.py",
        "from board.scores import top_scores\n\n\ndef test_best_first():\n"
        "    assert top_scores([3, 9, 1, 7], 2) == [9, 7]\n",
    ),
    "004_empty_input_crash": (
        "src/csvlite/parser.py",
        "    line = line.strip()\n",
        '    if line == "":\n        return []\n    line = line.strip()\n',
        "tests/test_empty_line.py",
        "from csvlite.parser import parse_csv_line\n\n\ndef test_empty_line():\n"
        '    assert parse_csv_line("") == []\n',
    ),
}

# The QEDCode modules the browser grader imports (verify/guard, grade and receipt and what they
# import), and stubs for the two imports a browser cannot satisfy and the grader never uses.
BROWSER_MODULES = [
    "__init__.py",
    "config.py",
    "models/__init__.py",
    "models/schemas.py",
    "engine/__init__.py",
    "engine/budget.py",
    "project/__init__.py",
    "project/paths.py",
    "verify/__init__.py",
    "verify/guard.py",
    "verify/grade.py",
    "verify/receipt.py",
    "verify/test_additions.py",
]
STUBS = {
    "tomli_w.py": '"""Browser stub: the grader never writes TOML."""\n\n\ndef dumps(*_a, **_k):\n'
    '    raise RuntimeError("not available in the browser")\n',
    "qedcode/vcs/__init__.py": "",
    "qedcode/vcs/git.py": '"""Browser stub: the grader never runs git."""\n\n\nclass GitError(RuntimeError):\n'
    '    pass\n\n\ndef git_out(*_a, **_k):\n    raise GitError("git is not available in the browser")\n',
}


def _files(folder: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(folder).as_posix()] = path.read_text(encoding="utf-8")
    return out


def build_levels() -> list[dict]:
    levels = []
    for name, title in LEVELS:
        zoo = ROOT / "bugzoo" / name
        files = {"pyproject.toml": (zoo / "pyproject.toml").read_text(encoding="utf-8")}
        files.update({f"src/{k}": v for k, v in _files(zoo / "src").items()})
        files.update({f"tests/{k}": v for k, v in _files(zoo / "tests").items()})
        hidden = {
            "tests/test_hidden.py": (zoo / "hidden" / "test_hidden.py").read_text(encoding="utf-8")
        }
        path, old, new, test_path, test_code = HARDCODE[name]
        assert old in files[path], (name, path)
        source = next(p for p in files if p.startswith("src/") and not p.endswith("__init__.py"))
        levels.append(
            {
                "id": name,
                "title": title,
                "task": (zoo / "task.txt").read_text(encoding="utf-8").strip(),
                "files": files,
                "hidden": hidden,
                "source": source,
                "tests": next(p for p in files if p.startswith("tests/")),
                "hardcode": {path: files[path].replace(old, new, 1), test_path: test_code},
            }
        )
    return levels


def copy_browser_modules(dest: Path) -> list[str]:
    written: list[str] = []
    for rel in BROWSER_MODULES:
        target = dest / "qedcode" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, target)
        written.append(f"qedcode/{rel}")
    for rel, text in STUBS.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(rel)
    return sorted(written)


def sample_receipt() -> str:
    """A receipt from a scripted `qed try` run on bug zoo 007 (no GPU, no network)."""
    os.environ.setdefault("QEDCODE_HOME", tempfile.mkdtemp(prefix="qedcode-site-home-"))
    sys.path.insert(0, str(ROOT / "src"))
    from qedcode import tour
    from qedcode.engine.callbacks import NonInteractiveCallbacks

    workdir = Path(tempfile.mkdtemp(prefix="qedcode-site-"))
    try:
        result = tour.run_tour(
            "007_sort_order", workdir, client=None, cb=NonInteractiveCallbacks(), scripted=True
        )
        if not result.solve.receipt_path:
            raise RuntimeError(f"The demo run made no receipt: {result.solve.message}")
        return Path(result.solve.receipt_path).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "website", out, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "assets" / "mark.svg", out / "mark.svg")
    (out / "play" / "levels.json").write_text(
        json.dumps(build_levels(), indent=1), encoding="utf-8"
    )
    manifest = copy_browser_modules(out / "play" / "py")
    (out / "play" / "py" / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    sys.path.insert(0, str(ROOT / "src"))
    from qedcode.models.schemas import Receipt

    schema = Receipt.model_json_schema()
    (out / "receipt" / "receipt.schema.json").write_text(
        json.dumps(schema, indent=1), encoding="utf-8"
    )
    (out / "receipt" / "sample.json").write_text(sample_receipt(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve folders that start with _
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "_site"))
    args = parser.parse_args(argv)
    print(f"built {build(Path(args.out))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
