"""repo.py chạy git thật trên repo tạm trong tmp_path (offline)."""
from pathlib import Path
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


# ---------- Hardening: chèn option git / hook / fsmonitor / env (review H2 #1, #10) ----------
def test_clone_rejects_option_injection(tmp_path):
    marker = tmp_path / "pwned"
    for url in (f"--config=core.fsmonitor=touch {marker}", "-u", f"--upload-pack=touch {marker}"):
        with pytest.raises(git.RepoError):
            git.clone(url, tmp_path / "dst")
    assert not marker.exists() and not (tmp_path / "dst").exists()


def test_clone_rejects_ext_transport(tmp_path):
    marker = tmp_path / "pwned"
    with pytest.raises(git.RepoError):
        git.clone(f"ext::sh -c touch% {marker}", tmp_path / "dst")
    assert not marker.exists()


def test_diff_and_changed_files_reject_option_base(tmp_path):
    r = git.init(tmp_path / "r")
    out = tmp_path / "out.txt"
    for bad in (f"--output={out}", "-p", "HEAD..main", "a b", "", "x\n"):
        with pytest.raises(git.RepoError):
            git.diff(r, base=bad)
        with pytest.raises(git.RepoError):
            git.list_changed_files(r, base=bad)
    assert not out.exists()


def test_worktree_rejects_option_base(tmp_path):
    r = git.init(tmp_path / "r")
    with pytest.raises(git.RepoError):
        git.create_worktree(r, "T1", base="--detach")
    with pytest.raises(git.RepoError):
        git.create_worktree(r, "-T1")
    assert git.list_worktrees(r) == [r.resolve()]


def test_valid_refs_still_work(tmp_path):
    r = git.init(tmp_path / "r")
    (r / "a").write_text("1")
    git.commit_all(r, "a")
    for ok in ("HEAD", "HEAD~1", "main", git._git(["rev-parse", "HEAD"], cwd=r).strip()):
        git.diff(r, base=ok)


def test_repo_hooks_and_fsmonitor_never_run(tmp_path):
    r = git.init(tmp_path / "r")
    marker = tmp_path / "hook_ran"
    hook = r / ".git/hooks/pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    cfg = r / ".git/config"
    cfg.write_text(cfg.read_text() + f"[core]\n\tfsmonitor = touch {marker}.fsm; false\n")
    (r / "x").write_text("x")
    git.list_changed_files(r)
    git.commit_all(r, "x")
    assert not marker.exists()
    assert not Path(f"{marker}.fsm").exists()


def test_inherited_git_env_ignored(tmp_path, monkeypatch):
    other = git.init(tmp_path / "other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    r = git.init(tmp_path / "r")
    (r / "f").write_text("1")
    assert git.list_changed_files(r) == ["f"]          # đọc đúng repo r, không bị GIT_DIR lái sang other
    assert git.list_changed_files(other) == []


# ---------- Worktree dùng lại khi chạy lại + dọn (review H2 #10) ----------
def test_ensure_worktree_reuses_existing(tmp_path):
    r = git.init(tmp_path / "r")
    wt = git.create_worktree(r, "T1")
    (wt / "keep.txt").write_text("dở dang")
    again = git.ensure_worktree(r, "T1")
    assert again == wt and (again / "keep.txt").exists()
    # worktree bị xoá nhưng nhánh task/T1 còn → gắn lại nhánh cũ, không blocked
    git.remove_worktree(r, wt, force=True)
    again2 = git.ensure_worktree(r, "T1")
    assert again2.exists()
    assert git._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=again2).strip() == "task/T1"


def test_ensure_worktree_refuses_foreign_dir(tmp_path):
    r = git.init(tmp_path / "r")
    d = tmp_path / "r.worktrees" / "T9"
    d.mkdir(parents=True)
    (d / "x").write_text("không phải worktree")
    with pytest.raises(git.RepoError):
        git.ensure_worktree(r, "T9")


def test_cleanup_task_worktrees(tmp_path):
    r = git.init(tmp_path / "r")
    w1, w2 = git.create_worktree(r, "A"), git.create_worktree(r, "B")
    removed = git.cleanup_task_worktrees(r, task_ids=["A"], delete_branch=True, force=True)
    assert removed == [w1] and not w1.exists() and w2.exists()
    assert "task/A" not in git._git(["branch", "--list"], cwd=r)
    removed = git.cleanup_task_worktrees(r, force=True)
    assert removed == [w2] and git.list_worktrees(r) == [r.resolve()]


# ================= Review cuối #5: không chạy code do repo cấu hình (diff/filter/merge driver) =================
def _evil_repo(tmp_path):
    import subprocess
    log = tmp_path / "pwned"
    sh = tmp_path / "evil.sh"
    sh.write_text(f"#!/bin/sh\necho \"$0 $*\" >> {log}\ncat\n")
    sh.chmod(0o755)
    r = git.init(tmp_path / "r")
    (r / "f.txt").write_text("a\n")
    (r / ".gitattributes").write_text("*.txt filter=x diff=t merge=m\n")
    git.commit_all(r, "f")                       # trước khi có config độc → commit sạch
    for k, v in [("diff.external", str(sh)), ("diff.t.textconv", str(sh)), ("diff.t.command", str(sh)),
                 ("filter.x.clean", str(sh)), ("filter.x.smudge", str(sh)),
                 ("filter.x.required", "true"), ("merge.m.driver", f"{sh} %A")]:
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    return r, log


def test_repo_config_cannot_run_code_via_diff_or_filters(tmp_path):
    r, log = _evil_repo(tmp_path)
    wt = git.ensure_worktree(r, "T1")
    (wt / "f.txt").write_text("b\n")
    d = git.diff(wt)
    assert "+b" in d and "-a" in d
    git.list_changed_files(wt, base="HEAD")
    git.commit_all(wt, "x")
    git.ensure_worktree(r, "T2")
    assert not log.exists(), log.read_text()


def test_repo_config_cannot_run_merge_driver(tmp_path):
    r, log = _evil_repo(tmp_path)
    a = git.ensure_worktree(r, "A")
    (a / "f.txt").write_text("A\n")
    git.commit_all(a, "A")
    b = git.ensure_worktree(r, "B")
    (b / "f.txt").write_text("B\n")
    git.commit_all(b, "B")
    with pytest.raises(git.MergeConflict):
        git.base_for_deps(r, "C", ["A", "B"])
    assert not log.exists(), log.read_text()
