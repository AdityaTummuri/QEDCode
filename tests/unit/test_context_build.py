from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from qedcode.models.schemas import Event, TaskSpec
from qedcode.project.context import (
    ContextFile,
    build_context,
    read_project_file,
    validate_located,
)
from qedcode.project.repo_map import list_python_files, repo_map, top_level_symbols
from qedcode.project.search import (
    Keyword,
    extract_keywords,
    format_hits,
    search,
)
from qedcode.project.tokens import estimate_tokens
from tests.conftest import BUGZOO_DIR, make_repo, run_git, write_files, zoo_project_names

# the file that holds the bug (or must receive the feature) in each bug zoo project
BUGGY = {
    "001_paginate_off_by_one": "src/shop/pages.py",
    "002_discount_threshold": "src/store/pricing.py",
    "003_mutable_default": "src/tagger/tags.py",
    "004_empty_input_crash": "src/csvlite/parser.py",
    "005_integer_average": "src/stats/core.py",
    "006_missing_config_key": "src/appconf/loader.py",
    "007_sort_order": "src/board/scores.py",
    "008_slugify_feature": "src/textutil/text.py",
    "009_factorial_zero": "src/mathx/numbers.py",
    "010_cart_quantity": "src/shopcart/cart.py",
}


def task_of(name: str) -> str:
    return (BUGZOO_DIR / name / "task.txt").read_text(encoding="utf-8").strip()


def spec_of(request: str, acceptance: list[str] | None = None) -> TaskSpec:
    return TaskSpec(id="t-ctx", request=request, acceptance=acceptance or [])


def words(keywords: list[Keyword]) -> dict[str, float]:
    best: dict[str, float] = {}
    for k in keywords:
        best[k.text] = max(best.get(k.text, 0.0), k.weight)
    return best


# ------------------------------------------------------------------ keywords


def test_keywords_from_a_realistic_task() -> None:
    kw = words(
        extract_keywords("Can you fix paginate()? It crashes with an IndexError in pages.py")
    )
    assert kw["paginate"] == 3.0  # identifier followed by brackets
    assert kw["IndexError"] == 2.0  # error name
    assert kw["pages.py"] == 4.0 and kw["pages"] == 4.0  # file name and its stem
    assert "Can" not in kw and "fix" not in kw  # filler words are not searched


def test_keywords_quoted_text_dotted_names_and_snake_case() -> None:
    kw = words(
        extract_keywords('load_config fails with KeyError: "timeout" for Cart.total and TokenStore')
    )
    assert kw["load_config"] == 3.0 and kw["timeout"] == 3.0
    assert kw["Cart.total"] == 3.0 and kw["Cart"] == 3.0 and kw["total"] == 3.0
    assert kw["TokenStore"] == 3.0 and kw["KeyError"] == 2.0


def test_plain_words_count_less_and_numbers_are_ignored() -> None:
    kw = words(extract_keywords("the discount should start at 100 or more", ["orders get 10% off"]))
    assert kw["discount"] == 1.0 and "100" not in kw and "10" not in kw
    assert "the" not in kw and "should" not in kw


def test_keywords_are_strongest_first_and_deduplicated() -> None:
    kws = extract_keywords("paginate paginate Paginate")
    assert [k.text for k in kws].count("paginate") <= 1
    assert [k.weight for k in kws] == sorted((k.weight for k in kws), reverse=True)


# ------------------------------------------------------------------ search on the bug zoo


@pytest.mark.parametrize("name", zoo_project_names())
def test_search_ranks_the_buggy_file_first_among_source_files(zoo_copy, name: str) -> None:
    project = zoo_copy(name)
    result = search(project, extract_keywords(task_of(name)))
    assert result.top_source_files(1) == [BUGGY[name]], (
        name,
        result.ranked_files[:4],
        result.scores,
    )
    assert result.hits and result.hits[0].path in result.ranked_files[:2]


def test_search_hits_are_formatted_with_two_lines_per_file(zoo_copy) -> None:
    project = zoo_copy("001_paginate_off_by_one")
    text = format_hits(search(project, extract_keywords("paginate page size")))
    lines = text.splitlines()
    assert lines and all(":" in ln for ln in lines)
    per_file: dict[str, int] = {}
    for ln in lines:
        per_file[ln.split(":")[0]] = per_file.get(ln.split(":")[0], 0) + 1
    assert max(per_file.values()) <= 2
    assert lines[0].startswith("src/shop/pages.py:")


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
def test_ripgrep_and_pure_python_search_agree(zoo_copy) -> None:
    project = zoo_copy("010_cart_quantity")
    kws = extract_keywords(task_of("010_cart_quantity"))
    with_rg = search(project, kws, use_ripgrep=True)
    without = search(project, kws, use_ripgrep=False)
    assert with_rg.ranked_files == without.ranked_files
    assert {(h.path, h.line, h.keyword) for h in with_rg.hits} == {
        (h.path, h.line, h.keyword) for h in without.hits
    }


def test_search_with_no_keywords_or_files(tmp_path: Path) -> None:
    make_repo(tmp_path / "r", {"a.py": "A = 1\n"})
    assert search(tmp_path / "r", []).hits == []
    assert search(tmp_path / "r", extract_keywords("zzzunknownword")).hits == []


# ------------------------------------------------------------------ repo map


MAP_FILES = {
    "src/shop/pages.py": "def paginate(items, page, size):\n    pass\n\n\ndef page_count(total, size):\n    pass\n",
    "src/shop/cart.py": "class Cart:\n    def add(self): pass\n    def total(self): pass\n    def __repr__(self): pass\n",
    "tests/test_pages.py": "def test_a(): pass\n",
    "notes.txt": "not python\n",
    ".gitignore": "ignored.py\n.venv/\n",
    "ignored.py": "def secret(): pass\n",
    ".venv/lib/site.py": "def venv_function(): pass\n",
}


def test_list_python_files_follows_git_and_skips_environments(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", MAP_FILES)
    write_files(repo, {"untracked_new.py": "def fresh(): pass\n"})
    assert list_python_files(repo) == [
        "src/shop/cart.py",
        "src/shop/pages.py",
        "tests/test_pages.py",
        "untracked_new.py",
    ]  # ignored.py and .venv/ are left out, untracked files are in


def test_list_python_files_without_git(tmp_path: Path) -> None:
    write_files(
        tmp_path, {"a.py": "A = 1\n", "node_modules/x.py": "X = 1\n", "pkg/b.py": "B = 1\n"}
    )
    assert list_python_files(tmp_path) == ["a.py", "pkg/b.py"]


def test_top_level_symbols() -> None:
    assert top_level_symbols(MAP_FILES["src/shop/cart.py"]) == [
        "Cart[add, total]"
    ]  # __repr__ hidden
    assert top_level_symbols("def b(:\n") == []
    many = "".join(f"def f{i}(): pass\n" for i in range(30))
    assert len(top_level_symbols(many, 12)) == 12


def test_repo_map_format(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", MAP_FILES)
    text = repo_map(repo, 1000)
    assert "src/shop/pages.py: paginate, page_count" in text
    assert "src/shop/cart.py: Cart[add, total]" in text
    assert "tests/test_pages.py: test_a" in text
    assert "secret" not in text and "venv_function" not in text


def test_repo_map_shrinks_to_fit_the_budget(tmp_path: Path) -> None:
    files = {
        f"src/pkg/mod_{i:03d}.py": "".join(f"def func_{j}(): pass\n" for j in range(10))
        for i in range(60)
    }
    files.update({f"tests/test_{i:03d}.py": "def test_x(): pass\n" for i in range(60)})
    repo = make_repo(tmp_path / "r", files)
    full = repo_map(repo, 100_000)
    assert "func_0" in full and "+" not in full.split("\n")[0][-8:]
    for budget in (1500, 700, 300):
        text = repo_map(repo, budget)
        assert estimate_tokens(text) <= budget, budget
    tiny = repo_map(repo, 300)
    assert "more files (not listed)" in tiny
    assert "src/pkg/mod_000.py" in tiny  # source files survive longer than tests
    assert sum(1 for ln in tiny.splitlines() if "tests/" in ln) < 60


# ------------------------------------------------------------------ validating the locator's answer


def test_validate_located_keeps_only_real_project_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"src/a.py": "A = 1\n", "docs/readme.md": "hi\n"})
    write_files(repo, {".venv/lib/x.py": "X = 1\n"})
    given = [
        "./src/a.py",
        "src/a.py",
        "src/missing.py",
        "../outside.py",
        "/etc/passwd",
        ".git/config",
        ".venv/lib/x.py",
        "docs/readme.md",
        "src",
        "",
        "src\\a.py",
    ]
    assert validate_located(repo, given) == ["src/a.py", "docs/readme.md"]
    assert read_project_file(repo, "src/a.py") == "A = 1\n"
    assert read_project_file(repo, "src/missing.py") is None
    assert read_project_file(repo, "src") is None


# ------------------------------------------------------------------ build_context


@pytest.mark.parametrize("name", zoo_project_names())
def test_for_every_bug_zoo_task_the_selected_files_include_the_buggy_file(
    zoo_copy, name: str
) -> None:
    project = zoo_copy(name)
    # a hopeless locator: the engine must still find the file (search fallback)
    ctx = build_context(
        project, spec_of(task_of(name)), lambda m, h: [], max_files=3, context_tokens=8192
    )
    assert BUGGY[name] in ctx.located, (name, ctx.located, ctx.notes)
    assert ctx.files and ctx.files[0].content
    assert any("best search matches" in n for n in ctx.notes)


@pytest.mark.parametrize("name", zoo_project_names())
def test_a_locator_that_picks_a_wrong_file_still_gets_the_buggy_one(zoo_copy, name: str) -> None:
    project = zoo_copy(name)
    wrong = "tests/" + next((project / "tests").iterdir()).name
    ctx = build_context(
        project, spec_of(task_of(name)), lambda m, h: [wrong], max_files=3, context_tokens=8192
    )
    assert BUGGY[name] in ctx.located and wrong in ctx.located
    assert any("best search match the locator did not pick" in n for n in ctx.notes)


def test_a_good_locator_is_trusted(zoo_copy) -> None:
    project = zoo_copy("001_paginate_off_by_one")
    shutil.rmtree(project / "hidden")  # QEDCode never sees the hidden tests
    seen: list[tuple[str, str]] = []

    def locate(repo_map_text: str, hits: str) -> list[str]:
        seen.append((repo_map_text, hits))
        return ["src/shop/pages.py"]

    ctx = build_context(
        project,
        spec_of(task_of("001_paginate_off_by_one")),
        locate,
        max_files=3,
        context_tokens=8192,
    )
    assert ctx.located == ["src/shop/pages.py"] and ctx.notes == []
    assert "src/shop/pages.py: paginate, page_count" in seen[0][0]
    assert seen[0][1].startswith("src/shop/pages.py:")  # the locator saw the search hits too
    assert ctx.related_tests == ["tests/test_pages.py"]
    assert ctx.example_test and ctx.example_test.path == "tests/test_pages.py"
    assert "def test_empty_items_give_an_empty_page" in ctx.example_test.content


def test_max_files_caps_the_result_and_keeps_the_extra_hit(zoo_copy) -> None:
    project = zoo_copy("001_paginate_off_by_one")
    ctx = build_context(
        project,
        spec_of(task_of("001_paginate_off_by_one")),
        lambda m, h: ["tests/test_pages.py", "src/shop/__init__.py"],
        max_files=2,
        context_tokens=8192,
    )
    assert len(ctx.located) == 2 and "src/shop/pages.py" in ctx.located
    assert ctx.located[0] == "tests/test_pages.py"


def test_invalid_locator_paths_are_dropped(zoo_copy) -> None:
    project = zoo_copy("003_mutable_default")
    picks = [
        "../secret.py",
        "/etc/passwd",
        ".git/config",
        "nope.py",
        "src/tagger/tags.py",
        "src/tagger/tags.py",
    ]
    ctx = build_context(
        project,
        spec_of(task_of("003_mutable_default")),
        lambda m, h: picks,
        max_files=5,
        context_tokens=8192,
    )
    assert ctx.located == ["src/tagger/tags.py"]


def test_with_nothing_to_go_on_the_first_source_files_are_shown(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path / "r",
        {
            "app/alpha.py": "A = 1\n",
            "app/beta.py": "B = 2\n",
            "tests/test_a.py": "def test_a(): pass\n",
        },
    )
    ctx = build_context(repo, spec_of("zzz qqq"), lambda m, h: [], max_files=2, context_tokens=8192)
    assert ctx.located == ["app/alpha.py", "app/beta.py"]
    assert any("first source files" in n for n in ctx.notes)


def test_events_and_compact_context(zoo_copy) -> None:
    project = zoo_copy("002_discount_threshold")
    events: list[Event] = []
    ctx = build_context(
        project,
        spec_of(task_of("002_discount_threshold")),
        lambda m, h: ["src/store/pricing.py"],
        max_files=3,
        context_tokens=8192,
        on_event=events.append,
    )
    assert (
        events[0].kind == "stage"
        and events[0].message == "Finding files ...... src/store/pricing.py"
    )
    small = ctx.compact(400)
    assert (
        small.files
        and small.repo_map == ""
        and small.hits == ""
        and "compact context" in small.notes
    )
    assert len(small.files) <= 3 and estimate_tokens(small.files[0].content) <= 400 + 150
    assert ctx.keywords and small.keywords == ctx.keywords


def test_a_small_context_window_shrinks_the_map_and_the_files(tmp_path: Path) -> None:
    big = "".join(f"def func_{i}(x):\n    return x + {i}\n\n\n" for i in range(300))
    repo = make_repo(tmp_path / "r", {"src/big.py": big, "src/other.py": "O = 1\n"})
    ctx = build_context(
        repo,
        spec_of("func_5 is wrong"),
        lambda m, h: ["src/big.py"],
        max_files=3,
        context_tokens=2700,
    )
    assert ctx.files[0].condensed  # the big file was cut down to fit
    assert estimate_tokens(ctx.files[0].content) < 1000
    assert "def func_5(x):\n    return x + 5" in ctx.files[0].content


# ------------------------------------------------------------------ a repo with 200 small files


def make_big_repo(tmp_path: Path) -> Path:
    files: dict[str, str] = {}
    for i in range(190):
        files[f"src/app/module_{i:03d}.py"] = f"def helper_{i}(value):\n    return value + {i}\n"
    files["src/app/billing/shipping.py"] = (
        "def calculate_shipping_tax(order, region):\n    return order * region\n"
    )
    files["src/app/billing/__init__.py"] = ""
    for i in range(8):
        files[f"tests/test_module_{i:03d}.py"] = (
            f"from app.module_{i:03d} import helper_{i}\n\ndef test_h():\n    assert helper_{i}(1)\n"
        )
    return make_repo(tmp_path / "big", files)


def test_the_target_is_found_among_200_files_quickly(tmp_path: Path) -> None:
    repo = make_big_repo(tmp_path)
    assert len(list_python_files(repo)) >= 199
    request = "calculate_shipping_tax gives the wrong amount for some regions"
    started = time.monotonic()
    ctx = build_context(repo, spec_of(request), lambda m, h: [], max_files=5, context_tokens=4096)
    assert time.monotonic() - started < 5
    assert ctx.located[0] == "src/app/billing/shipping.py"
    assert estimate_tokens(ctx.repo_map) <= 4096
    assert ctx.hits.splitlines()[0].startswith("src/app/billing/shipping.py:")


def test_the_big_repo_map_stays_within_a_small_budget(tmp_path: Path) -> None:
    repo = make_big_repo(tmp_path)
    for budget in (2000, 800):
        assert estimate_tokens(repo_map(repo, budget)) <= budget


def test_the_locator_sees_a_map_that_fits_its_prompt(tmp_path: Path) -> None:
    repo = make_big_repo(tmp_path)
    sizes: list[int] = []
    build_context(
        repo,
        spec_of("calculate_shipping_tax is wrong"),
        lambda m, h: sizes.append(estimate_tokens(m) + estimate_tokens(h)) or [],
        max_files=5,
        context_tokens=2048,
    )
    assert sizes and sizes[0] < 2048 - 200 - 512  # context - locator reply - margin
    assert ContextFile("x", "y").condensed is False
    run_git(repo, "status", "--porcelain")


@pytest.mark.skipif(sys.platform == "win32", reason="the fake rg is a shell-executable script")
def test_the_ripgrep_path_parses_rg_output_like_the_python_search(
    zoo_copy, tmp_path, monkeypatch
) -> None:
    fake = tmp_path / "bin" / "rg"
    fake.parent.mkdir()
    fake.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "patterns = [args[i + 1].lower() for i, a in enumerate(args) if a == '-e']\n"
        "files = args[args.index('--') + 1:]\n"
        "found = False\n"
        "for name in files:\n"
        "    for n, line in enumerate(open(name, encoding='utf-8'), 1):\n"
        "        if any(p in line.lower() for p in patterns):\n"
        "            print(f'{name}:{n}:{line.rstrip()}'); found = True\n"
        "sys.exit(0 if found else 1)\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("rg") == str(fake)
    project = zoo_copy("010_cart_quantity")
    kws = extract_keywords(task_of("010_cart_quantity"))
    via_rg = search(project, kws, use_ripgrep=True)
    pure = search(project, kws, use_ripgrep=False)
    assert via_rg.ranked_files == pure.ranked_files and via_rg.hits
    assert {(h.path, h.line, h.keyword) for h in via_rg.hits} == {
        (h.path, h.line, h.keyword) for h in pure.hits
    }
    # when rg fails (exit code 2) the search falls back to pure Python
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(2)\n")
    assert search(project, kws).ranked_files == pure.ranked_files
