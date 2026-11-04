"""Git cho Dispatcher (qua adapter src/tools/integrations/repo.py — cục bộ, không push).

Worktree task/<id>: task con tạo từ NHÁNH CỦA TASK CHA (task/<dep>); nhiều dep → nhánh tích hợp tạm
integ/<id> gộp các nhánh dep (xung đột → MergeConflict → caller chuyển task sang blocked).
"""
from __future__ import annotations

from pathlib import Path

from src.tools.integrations import repo as git

MergeConflict = git.MergeConflict


def worktree_for(repo: str | Path, task: dict) -> str:
    """Worktree của task. Nhánh task/<id> đã có (chạy lại) → gắn lại, không đổi base."""
    tid = task["id"]
    if git.branch_exists(repo, git.branch_name(tid)):
        return str(git.ensure_worktree(repo, tid))
    base = git.base_for_deps(repo, tid, list(task.get("deps") or []))
    try:
        return str(git.ensure_worktree(repo, tid, base=base))
    finally:
        git.drop_integ_branch(repo, tid)


def commit_worktree(worktree: str | None, tid: str) -> None:
    """Task đạt await_merge: chốt việc trong worktree thành commit cục bộ trên task/<id> để
    task con (tạo từ nhánh này) thấy được. Không merge, không push."""
    if worktree and git.has_changes(worktree):
        git.commit_all(worktree, f"task {tid}: await_merge (dispatcher)")
