from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from qedcode.models.ids import new_task_id
from qedcode.vcs import worktree
from qedcode.vcs.git import GitError, git_dir, git_out, has_commits, is_git_repo, repo_root
from qedcode.vcs.patch import (
    LAST_APPLY,
    PatchRefused,
    apply_to_user,
    last_apply,
    patch_files,
    undo_last,
)
from qedcode.vcs.snapshot import snapshot, snapshot_ref
from tests.conftest import make_repo, run_git, tree_state, write_files

BASE = {
    "app/main.py": "def add(a, b):\n    return a + b\n",
    "app/util.py": "X = 1\n",
    "README.md": "# demo\n",
    ".gitignore": "ignored.log\nbuild/\n",
}


def lsfiles(repo: Path, commit: str) -> list[str]:
    out = run_git(repo, "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", commit)
    return out.split("\n")[:-1]


def show(repo: Path, commit: str, path: str) -> str:
    return run_git(repo, "show", f"{commit}:{path}")


# ------------------------------------------------------------------ git helpers


def test_git_helpers(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    assert is_git_repo(repo)
    assert not is_git_repo(tmp_path)
    assert repo_root(repo / "app") == repo.resolve() or repo_root(repo / "app").samefile(repo)
    assert has_commits(repo)
    assert git_dir(repo).name == ".git"
    with pytest.raises(GitError) as err:
        git_out(tmp_path, "log")
    assert "git log" in str(err.value) and "git status" in str(err.value)


def test_new_task_id_shape_and_uniqueness() -> None:
    first, second = new_task_id(), new_task_id()
    assert re.fullmatch(r"t-\d{8}-\d{6}-[0-9a-f]{4}", first)
    assert first != second


# ------------------------------------------------------------------ snapshots


def test_snapshot_of_a_clean_repo_matches_head(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    commit = snapshot(repo, "t-1")
    assert run_git(repo, "rev-parse", f"{commit}^{{tree}}") == run_git(
        repo, "rev-parse", "HEAD^{tree}"
    )
    assert run_git(repo, "rev-parse", f"{commit}^") == run_git(repo, "rev-parse", "HEAD")
    assert run_git(repo, "rev-parse", snapshot_ref("t-1")).strip() == commit


def test_snapshot_captures_dirty_and_untracked_but_not_ignored(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    write_files(
        repo,
        {
            "app/main.py": "def add(a, b):\n    return a + b + 0  # dirty\n",
            "app/new_module.py": "NEW = True\n",
            "ignored.log": "noise\n",
            "build/out.txt": "artifact\n",
        },
    )
    (repo / "README.md").unlink()  # deleted tracked file
    commit = snapshot(repo, "t-2")
    files = lsfiles(repo, commit)
    assert "app/new_module.py" in files
    assert "ignored.log" not in files and "build/out.txt" not in files
    assert "README.md" not in files  # the deletion is part of the snapshot
    assert not any(f.startswith(".qed") for f in files)
    assert "dirty" in show(repo, commit, "app/main.py")
    assert show(repo, commit, "app/new_module.py") == "NEW = True\n"


def test_snapshot_in_a_repo_with_no_commits(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": "A = 1\n", "b/c.py": "C = 3\n"}, commit=False)
    assert not has_commits(repo)
    commit = snapshot(repo, "t-3")
    assert sorted(lsfiles(repo, commit)) == ["a.py", "b/c.py"]
    assert run_git(repo, "rev-list", "--parents", "-n", "1", commit).strip() == commit  # no parent
    assert not has_commits(repo)  # the user's branch is still unborn


def test_snapshot_handles_spaces_and_unicode_in_paths(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "my repo", {"src/my module.py": "A = 1\n"})
    write_files(repo, {"src/new file.py": "B = 2\n", "src/naïve.py": "C = 3\n"})
    commit = snapshot(repo, "t-4")
    files = lsfiles(repo, commit)
    assert "src/new file.py" in files and "src/naïve.py" in files and "src/my module.py" in files


def test_snapshot_never_touches_the_users_repo(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    write_files(repo, {"app/util.py": "X = 2\n", "app/extra.py": "E = 1\n", "ignored.log": "x\n"})
    status_before = run_git(repo, "status", "--porcelain=v1", "-z")  # may refresh the index
    index_before = (repo / ".git" / "index").read_bytes()  # so read the index after it
    files_before = tree_state(repo)
    head_before = run_git(repo, "rev-parse", "HEAD")
    branches_before = run_git(repo, "branch", "--list")
    snapshot(repo, "t-5")
    snapshot(repo, "t-6")
    assert (repo / ".git" / "index").read_bytes() == index_before
    assert run_git(repo, "status", "--porcelain=v1", "-z") == status_before
    assert tree_state(repo) == files_before
    assert run_git(repo, "rev-parse", "HEAD") == head_before
    assert run_git(repo, "branch", "--list") == branches_before
    assert list((repo / ".qed" / "tmp").glob("index-*")) == []  # the temporary index is removed


def test_qed_folder_is_excluded_locally_once(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    snapshot(repo, "t-7")
    snapshot(repo, "t-8")
    exclude = (git_dir(repo) / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.count(".qed/") == 1
    assert ".qed" not in (repo / ".gitignore").read_text(encoding="utf-8")
    assert run_git(repo, "status", "--porcelain").strip() == ""  # .qed/ never shows up


def test_heavy_untracked_folders_are_left_out_even_without_a_gitignore(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": "A = 1\n"})
    write_files(
        repo,
        {
            ".venv/lib/site.py": "venv\n",
            "pkg/__pycache__/x.pyc": "pyc\n",
            "node_modules/dep/index.js": "js\n",
            "real.py": "R = 1\n",
        },
    )
    files = lsfiles(repo, snapshot(repo, "t-9"))
    assert files == ["a.py", "real.py"]


# ------------------------------------------------------------------ worktrees


def test_worktree_create_commit_diff_remove(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    write_files(repo, {"app/util.py": "X = 5\n"})  # a dirty change in the user's tree
    base = snapshot(repo, "t-10")
    user_files = tree_state(repo)
    wt = worktree.create(repo, base, "t-10-c1")
    assert wt == repo / ".qed" / "worktrees" / "t-10-c1"
    assert (wt / "app" / "util.py").read_text() == "X = 5\n"  # starts from the snapshot
    write_files(
        wt, {"app/main.py": "def add(a, b):\n    return a + b + 1\n", "app/more.py": "M = 1\n"}
    )
    (wt / "README.md").unlink()
    commit = worktree.commit_all(wt, "candidate c1")
    assert commit != base
    assert git_out(wt, "rev-parse", "HEAD") == commit
    patch = worktree.diff(repo, base, commit)
    assert "+    return a + b + 1" in patch and "app/more.py" in patch
    assert "deleted file mode" in patch  # the deletion is visible to the scope check later
    assert tree_state(repo) == user_files  # the user's tree was never touched
    assert worktree.list_worktrees(repo) == [wt]
    worktree.remove(repo, wt)
    assert not wt.exists() and worktree.list_worktrees(repo) == []
    assert "t-10-c1" not in run_git(repo, "worktree", "list")


def test_commit_all_with_no_changes_still_commits(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-11")
    wt = worktree.create(repo, base, "c1")
    commit = worktree.commit_all(wt, "nothing changed")
    assert worktree.diff(repo, base, commit) == ""
    worktree.remove(repo, wt)


def test_worktree_names_cannot_escape_the_folder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-12")
    wt = worktree.create(repo, base, "../../evil name")
    assert wt.parent == repo / ".qed" / "worktrees"
    assert wt.name == "evil-name" or wt.name.endswith("evil-name")
    worktree.remove_all(repo)


def test_remove_all_and_prune(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-13")
    worktree.create(repo, base, "a")
    b = worktree.create(repo, base, "b")
    assert worktree.remove_all(repo) == 2
    assert worktree.list_worktrees(repo) == []
    # a crashed run leaves a stale registration: prune clears it
    c = worktree.create(repo, base, "c")
    import shutil

    shutil.rmtree(c)
    assert "worktrees/c" in run_git(repo, "worktree", "list", "--porcelain").replace("\\", "/")
    worktree.prune(repo)
    assert "worktrees/c" not in run_git(repo, "worktree", "list", "--porcelain").replace("\\", "/")
    assert not b.exists()


def test_creating_the_same_worktree_twice_replaces_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-14")
    first = worktree.create(repo, base, "c1")
    (first / "junk.txt").write_text("x")
    second = worktree.create(repo, base, "c1")
    assert first == second and not (second / "junk.txt").exists()
    worktree.remove_all(repo)


# ------------------------------------------------------------------ apply and undo


def make_candidate(repo: Path, base: str, changes: dict[str, str | bytes], delete=()) -> str:
    wt = worktree.create(repo, base, "cand")
    write_files(wt, changes)
    for rel in delete:
        (wt / rel).unlink()
    commit = worktree.commit_all(wt, "candidate")
    worktree.remove(repo, wt)
    return worktree.diff(repo, base, commit)


def test_apply_changes_only_the_working_tree_and_undo_restores_everything(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    write_files(repo, {"app/util.py": "X = 99  # my own edit\n"})  # unrelated dirty file
    base = snapshot(repo, "t-20")
    patch = make_candidate(
        repo,
        base,
        {"app/main.py": "def add(a, b):\n    return b + a\n", "app/created.py": "NEW = 1\n"},
        delete=["README.md"],
    )
    before = tree_state(repo)
    index_before = (repo / ".git" / "index").read_bytes()
    result = apply_to_user(repo, base, patch, task_id="t-20")
    assert sorted(result.files) == ["README.md", "app/created.py", "app/main.py"]
    assert (repo / "app" / "main.py").read_text() == "def add(a, b):\n    return b + a\n"
    assert (repo / "app" / "created.py").exists() and not (repo / "README.md").exists()
    assert (repo / "app" / "util.py").read_text() == "X = 99  # my own edit\n"  # untouched
    assert (repo / ".git" / "index").read_bytes() == index_before  # never the index
    record = last_apply(repo)
    assert record and record["task_id"] == "t-20" and record["snapshot"] == base
    assert (repo / LAST_APPLY).exists()
    undone = undo_last(repo)
    assert sorted(undone) == sorted(result.files)
    assert tree_state(repo) == before  # byte for byte, including the deleted file
    assert not (repo / "app" / "created.py").exists()
    assert not (repo / LAST_APPLY).exists()
    assert (repo / ".git" / "index").read_bytes() == index_before


def test_apply_refuses_when_a_touched_file_changed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-21")
    patch = make_candidate(repo, base, {"app/main.py": "def add(a, b):\n    return 0\n"})
    write_files(repo, {"app/main.py": "def add(a, b):\n    return a + b  # edited meanwhile\n"})
    before = tree_state(repo)
    with pytest.raises(PatchRefused) as err:
        apply_to_user(repo, base, patch)
    assert "app/main.py" in str(err.value) and err.value.files == ["app/main.py"]
    assert "left alone" in str(err.value)
    assert tree_state(repo) == before
    assert last_apply(repo) is None


def test_apply_refuses_when_a_new_file_now_exists_or_a_deleted_one_changed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-22")
    patch = make_candidate(repo, base, {"brand_new.py": "A = 1\n"}, delete=["README.md"])
    write_files(repo, {"brand_new.py": "mine\n"})
    with pytest.raises(PatchRefused) as err:
        apply_to_user(repo, base, patch)
    assert err.value.files == ["brand_new.py"]
    (repo / "brand_new.py").unlink()
    write_files(repo, {"README.md": "# changed\n"})
    with pytest.raises(PatchRefused) as err2:
        apply_to_user(repo, base, patch)
    assert err2.value.files == ["README.md"]


def test_apply_refuses_a_file_the_user_deleted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-23")
    patch = make_candidate(repo, base, {"app/util.py": "X = 2\n"})
    (repo / "app" / "util.py").unlink()
    with pytest.raises(PatchRefused):
        apply_to_user(repo, base, patch)


def test_apply_with_an_empty_patch_is_refused_clearly(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    with pytest.raises(PatchRefused) as err:
        apply_to_user(repo, snapshot(repo, "t-24"), "")
    assert "nothing to apply" in str(err.value)


def test_undo_refuses_when_a_file_changed_after_apply(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-25")
    patch = make_candidate(repo, base, {"app/main.py": "def add(a, b):\n    return 42\n"})
    apply_to_user(repo, base, patch, task_id="t-25")
    write_files(repo, {"app/main.py": "def add(a, b):\n    return 42  # then I edited it\n"})
    before = tree_state(repo)
    with pytest.raises(PatchRefused) as err:
        undo_last(repo)
    assert "app/main.py" in str(err.value) and "left alone" in str(err.value)
    assert tree_state(repo) == before
    assert last_apply(repo) is not None  # still undoable once the user fixes the file


def test_undo_with_nothing_applied(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    with pytest.raises(PatchRefused) as err:
        undo_last(repo)
    assert "nothing to undo" in str(err.value)


def test_binary_files_crlf_files_and_spaces_survive_apply_and_undo(tmp_path: Path) -> None:
    binary = bytes(range(256)) * 4
    repo = make_repo(
        tmp_path / "my repo",
        {"data/blob.bin": binary, "src/win file.txt": "one\r\ntwo\r\n", "src/a b.py": "A = 1\n"},
    )
    base = snapshot(repo, "t-26")
    new_binary = bytes(reversed(binary))
    patch = make_candidate(
        repo,
        base,
        {
            "data/blob.bin": new_binary,
            "src/win file.txt": "one\r\nTWO\r\nthree\r\n",
            "src/a b.py": "A = 2\n",
        },
    )
    assert "\r\n" in patch  # the CRLF lines are in the patch exactly
    before = tree_state(repo)
    apply_to_user(repo, base, patch)
    assert (repo / "data" / "blob.bin").read_bytes() == new_binary
    assert (repo / "src" / "win file.txt").read_bytes() == b"one\r\nTWO\r\nthree\r\n"
    assert (repo / "src" / "a b.py").read_text() == "A = 2\n"
    undo_last(repo)
    assert tree_state(repo) == before


def test_patch_files_lists_touched_paths(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a b.py": "A = 1\n", "c.py": "C = 1\n"})
    base = snapshot(repo, "t-27")
    patch = make_candidate(repo, base, {"a b.py": "A = 2\n", "d.py": "D = 1\n"}, delete=["c.py"])
    assert sorted(patch_files(repo, patch)) == ["a b.py", "c.py", "d.py"]


def test_last_apply_file_is_valid_json_with_hashes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", BASE)
    base = snapshot(repo, "t-28")
    patch = make_candidate(repo, base, {"app/util.py": "X = 3\n"})
    apply_to_user(repo, base, patch, task_id="t-28")
    data = json.loads((repo / LAST_APPLY).read_text(encoding="utf-8"))
    assert data["files"] == ["app/util.py"]
    assert data["before"]["app/util.py"] != data["after"]["app/util.py"]
    assert data["patch"] == patch
