"""repo.py chạy git thật trên repo tạm trong tmp_path (offline)."""
import pytest
from src.tools.integrations import repo as git


def test_create_change_diff_remove_worktree(tmp_path):
    r = git.init(tmp_path / "r")
    (r / "a.txt").write_text("v1\n")
    git.commit_all(r, "add a")

    wt = git.create_worktree(r, "T1")
    assert wt.exists() and wt in [p.resolve() for p in git.list_worktrees(r)]
    assert git._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt).strip() == "task/T1"

    (wt / "a.txt").write_text("v2\n")
    (wt / "new.py").write_text("x = 1\n")
    assert git.list_changed_files(wt) == ["a.txt", "new.py"]
    assert "+v2" in git.diff(wt)

    git.commit_all(wt, "T1 work")
    assert git.list_changed_files(wt) == []
    assert git.list_changed_files(wt, base="main") == ["a.txt", "new.py"]
    assert (r / "a.txt").read_text() == "v1\n"  # repo chính không bị đụng

    git.remove_worktree(r, wt, force=True, delete_branch=True, task_id="T1")
    assert not wt.exists()
    assert "task/T1" not in git._git(["branch", "--list"], cwd=r)


def test_two_worktrees_isolated(tmp_path):
    r = git.init(tmp_path / "r")
    w1, w2 = git.create_worktree(r, "A"), git.create_worktree(r, "B")
    (w1 / "f").write_text("1")
    assert git.list_changed_files(w1) == ["f"] and git.list_changed_files(w2) == []
    with pytest.raises(git.RepoError):
        git.create_worktree(r, "A")  # trùng


def test_clone_local(tmp_path):
    src = git.init(tmp_path / "src")
    (src / "x").write_text("x")
    git.commit_all(src, "x")
    dst = git.clone(str(src), tmp_path / "dst")
    assert (dst / "x").read_text() == "x"


def test_guards_no_push_and_bad_task_id(tmp_path):
    r = git.init(tmp_path / "r")
    with pytest.raises(git.RepoError):
        git._git(["push", "origin", "main"], cwd=r)
    with pytest.raises(git.RepoError):
        git.create_worktree(r, "../escape")
