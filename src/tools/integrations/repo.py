"""Adapter repo: git thật qua subprocess (không thư viện ngoài).

Quyền tối thiểu:
- Chỉ thao tác cục bộ: init, clone (đọc), worktree, branch task/<id>, commit cục bộ, diff.
- KHÔNG push / fetch ghi remote. Mọi lệnh đi qua `_git`, lệnh ngoài whitelist bị từ chối.
- Không hỏi credential (GIT_TERMINAL_PROMPT=0), không ký commit.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_ALLOWED = {"init", "clone", "worktree", "branch", "status", "diff", "rev-parse",
            "add", "commit", "log", "show-ref"}
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BOT = ["-c", "user.name=outsourceAgent", "-c", "user.email=bot@outsourceagent.local",
        "-c", "commit.gpgsign=false", "-c", "credential.helper="]


class RepoError(Exception):
    pass


def _git(args: list[str], cwd: Path | str | None = None, timeout: int = 60) -> str:
    sub = args[0] if args else None
    if sub not in _ALLOWED:
        raise RepoError(f"lệnh git '{sub}' không nằm trong whitelist (không push/remote)")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "LC_ALL": "C"}
    p = subprocess.run(["git", *_BOT, *args], cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=env)
    if p.returncode != 0:
        raise RepoError(f"git {' '.join(args)} lỗi: {p.stderr.strip()}")
    return p.stdout


def _check_task_id(task_id: str) -> None:
    if not _TASK_ID.match(task_id or "") or ".." in task_id:
        raise RepoError(f"task_id không hợp lệ: {task_id!r}")


def init(path: Path | str, initial_commit: bool = True, branch: str = "main") -> Path:
    """Tạo repo mới. initial_commit=True để có HEAD (worktree cần base)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", branch], cwd=path)
    if initial_commit:
        _git(["commit", "-q", "--allow-empty", "-m", "init"], cwd=path)
    return path


def clone(url: str, dest: Path | str) -> Path:
    """Clone chỉ đọc. Không cấu hình gì để push."""
    dest = Path(dest)
    _git(["clone", "-q", "--no-recurse-submodules", url, str(dest)], timeout=300)
    return dest


def branch_name(task_id: str) -> str:
    _check_task_id(task_id)
    return f"task/{task_id}"


def create_worktree(repo: Path | str, task_id: str, base: str = "HEAD",
                    root: Path | str | None = None) -> Path:
    """Tạo worktree riêng cho task trên nhánh task/<id>. Mặc định đặt ở <repo>.worktrees/<id>
    (ngoài repo chính để không thành file untracked)."""
    repo = Path(repo).resolve()
    br = branch_name(task_id)
    root = Path(root) if root else repo.parent / f"{repo.name}.worktrees"
    wt = (root / task_id).resolve()
    if wt.exists():
        raise RepoError(f"worktree đã tồn tại: {wt}")
    root.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", br, str(wt), base], cwd=repo)
    return wt


def list_worktrees(repo: Path | str) -> list[Path]:
    out = _git(["worktree", "list", "--porcelain"], cwd=repo)
    return [Path(line.split(" ", 1)[1]) for line in out.splitlines() if line.startswith("worktree ")]


def list_changed_files(worktree: Path | str, base: str | None = None) -> list[str]:
    """File đổi trong worktree: chưa commit (kể cả untracked) + đã commit so với `base` (nếu có)."""
    files: set[str] = set()
    for line in _git(["status", "--porcelain", "-uall"], cwd=worktree).splitlines():
        path = line[3:]
        files.add(path.split(" -> ", 1)[-1])
    if base:
        files.update(f for f in _git(["diff", "--name-only", f"{base}...HEAD"], cwd=worktree).splitlines() if f)
    return sorted(files)


def diff(worktree: Path | str, base: str = "HEAD") -> str:
    """Diff file đã track (working tree so với `base`). File untracked không có trong diff."""
    return _git(["diff", base], cwd=worktree)


def commit_all(worktree: Path | str, message: str) -> str:
    """Commit cục bộ trên nhánh task. Trả commit sha."""
    _git(["add", "-A"], cwd=worktree)
    _git(["commit", "-q", "-m", message], cwd=worktree)
    return _git(["rev-parse", "HEAD"], cwd=worktree).strip()


def remove_worktree(repo: Path | str, worktree: Path | str, force: bool = False,
                    delete_branch: bool = False, task_id: str | None = None) -> None:
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(worktree)]
    _git(args, cwd=repo)
    _git(["worktree", "prune"], cwd=repo)
    if delete_branch:
        if not task_id:
            raise RepoError("delete_branch cần task_id")
        _git(["branch", "-D" if force else "-d", branch_name(task_id)], cwd=repo)
