"""Adapter repo: git thật qua subprocess (không thư viện ngoài).

Quyền tối thiểu:
- Chỉ thao tác cục bộ: init, clone (đọc), worktree, branch task/<id>, commit cục bộ, diff.
- KHÔNG push / fetch ghi remote. Mọi lệnh đi qua `_git`, lệnh ngoài whitelist bị từ chối.
- Không hỏi credential (GIT_TERMINAL_PROMPT=0), không ký commit.
- Chống chèn option: tham số vị trí luôn đứng sau `--` / `--end-of-options`; url/ref/path bắt đầu
  bằng `-` bị từ chối; ref phải qua `_check_ref`.
- Không chạy code do repo cấu hình: core.fsmonitor tắt, core.hooksPath=/dev/null, transport ext:: cấm.
- Không kế thừa biến môi trường GIT_* (GIT_DIR, GIT_CONFIG_*, GIT_SSH_COMMAND...) của tiến trình cha.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_ALLOWED = {"init", "clone", "worktree", "branch", "status", "diff", "rev-parse",
            "add", "commit", "log", "show-ref", "switch", "merge"}
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Revision hợp lệ: tên nhánh/tag (có thể có '/'), sha, HEAD, hậu tố ~N / ^N. Không khoảng trắng,
# không ký tự điều khiển, không ':' '?' '*' '[' '\\', không '..', không bắt đầu bằng '-'.
_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}([~^][0-9]{0,4})*$")
_BOT = ["-c", "user.name=outsourceAgent", "-c", "user.email=bot@outsourceagent.local",
        "-c", "commit.gpgsign=false", "-c", "credential.helper=",
        "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
        "-c", "protocol.ext.allow=never"]
_ENV_EXTRA = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "LC_ALL": "C",
              "GIT_CONFIG_NOSYSTEM": "1"}


class RepoError(Exception):
    pass


class MergeConflict(RepoError):
    """Merge các nhánh dep xung đột. `files`: file xung đột, `branches`: nhánh đã thử gộp."""

    def __init__(self, msg: str, files: list[str], branches: list[str]):
        super().__init__(msg)
        self.files, self.branches = files, branches


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_ENV_EXTRA)
    return env


def _git(args: list[str], cwd: Path | str | None = None, timeout: int = 60) -> str:
    sub = args[0] if args else None
    if sub not in _ALLOWED:
        raise RepoError(f"lệnh git '{sub}' không nằm trong whitelist (không push/remote)")
    p = subprocess.run(["git", *_BOT, *args], cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=_env())
    if p.returncode != 0:
        raise RepoError(f"git {' '.join(args)} lỗi: {p.stderr.strip()}")
    return p.stdout


def _check_task_id(task_id: str) -> None:
    if not _TASK_ID.match(task_id or "") or ".." in task_id:
        raise RepoError(f"task_id không hợp lệ: {task_id!r}")


def _check_ref(ref: str) -> str:
    """Revision do caller đưa vào (base của diff/worktree). Từ chối mọi thứ trông như option."""
    if (not isinstance(ref, str) or not _REF.match(ref) or ".." in ref or "//" in ref
            or ref.endswith(("/", ".", ".lock")) or "/." in ref):
        raise RepoError(f"ref không hợp lệ: {ref!r}")
    return ref


def _check_branch(name: str) -> str:
    _check_ref(name)
    if any(c in name for c in "~^"):
        raise RepoError(f"tên nhánh không hợp lệ: {name!r}")
    return name


def _check_path(p: Path | str) -> str:
    s = str(p)
    if not s or s.startswith("-"):
        raise RepoError(f"đường dẫn không hợp lệ: {s!r}")
    return s


def _check_url(url: str) -> str:
    if not isinstance(url, str) or not url or url.startswith("-") or any(c in url for c in "\n\r\0"):
        raise RepoError(f"url không hợp lệ: {url!r}")
    if "::" in url.split("/", 1)[0]:          # transport helper (ext::, fd::...) → có thể chạy lệnh
        raise RepoError(f"transport '{url.split('::', 1)[0]}::' bị cấm")
    return url


def init(path: Path | str, initial_commit: bool = True, branch: str = "main") -> Path:
    """Tạo repo mới. initial_commit=True để có HEAD (worktree cần base)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _check_branch(branch)
    _git(["init", "-q", "-b", branch, "--", _check_path(path.resolve())], cwd=path)
    if initial_commit:
        _git(["commit", "-q", "--allow-empty", "-m", "init"], cwd=path)
    return path


def clone(url: str, dest: Path | str) -> Path:
    """Clone chỉ đọc. Không cấu hình gì để push."""
    dest = Path(dest)
    _git(["clone", "-q", "--no-recurse-submodules", "--", _check_url(url), _check_path(dest.resolve())],
         timeout=300)
    return dest


def branch_name(task_id: str) -> str:
    _check_task_id(task_id)
    return f"task/{task_id}"


def current_branch(repo: Path | str) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()


def branch_exists(repo: Path | str, name: str) -> bool:
    _check_branch(name)
    try:
        _git(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], cwd=repo)
        return True
    except RepoError:
        return False


def switch_branch(repo: Path | str, name: str, create: bool = False) -> None:
    """Chuyển nhánh cục bộ (create=True: tạo mới từ HEAD). Không đụng remote."""
    _check_branch(name)
    _git(["switch", "-q", *(["-c"] if create else []), name], cwd=repo)


def _wt_root(repo: Path, root: Path | str | None) -> Path:
    return Path(root) if root else repo.parent / f"{repo.name}.worktrees"


def create_worktree(repo: Path | str, task_id: str, base: str = "HEAD",
                    root: Path | str | None = None) -> Path:
    """Tạo worktree riêng cho task trên nhánh task/<id>. Mặc định đặt ở <repo>.worktrees/<id>
    (ngoài repo chính để không thành file untracked). Đã tồn tại → RepoError (dùng ensure_worktree
    khi chạy lại)."""
    repo = Path(repo).resolve()
    br = branch_name(task_id)
    _check_ref(base)
    wt = (_wt_root(repo, root) / task_id).resolve()
    if wt.exists():
        raise RepoError(f"worktree đã tồn tại: {wt}")
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", br, "--", _check_path(wt), base], cwd=repo)
    return wt


def _worktree_entries(repo: Path | str) -> list[dict]:
    out = _git(["worktree", "list", "--porcelain"], cwd=repo)
    entries, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                entries.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v
    return entries


def ensure_worktree(repo: Path | str, task_id: str, base: str = "HEAD",
                    root: Path | str | None = None) -> Path:
    """Như create_worktree nhưng chạy lại được (attempt mới):
    - worktree của task/<id> đã có ở đúng chỗ → dùng lại (giữ việc dở);
    - nhánh task/<id> còn mà worktree mất → gắn lại nhánh đó;
    - thư mục tồn tại nhưng không phải worktree của task/<id> → RepoError (không đè)."""
    repo = Path(repo).resolve()
    br = branch_name(task_id)
    _check_ref(base)
    wt = (_wt_root(repo, root) / task_id).resolve()
    _git(["worktree", "prune"], cwd=repo)
    for e in _worktree_entries(repo):
        if Path(e.get("worktree", "")).resolve() == wt:
            if e.get("branch") != f"refs/heads/{br}":
                raise RepoError(f"{wt} là worktree của {e.get('branch')}, không phải {br}")
            return wt
    if wt.exists():
        raise RepoError(f"{wt} tồn tại nhưng không phải worktree của {br} — không ghi đè")
    wt.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, br):
        _git(["worktree", "add", "-q", "--", _check_path(wt), br], cwd=repo)
    else:
        _git(["worktree", "add", "-q", "-b", br, "--", _check_path(wt), base], cwd=repo)
    return wt


INTEG_PREFIX = "integ/"


def integ_branch_name(task_id: str) -> str:
    _check_task_id(task_id)
    return f"{INTEG_PREFIX}{task_id}"


def has_changes(worktree: Path | str) -> bool:
    return bool(_git(["status", "--porcelain", "-uall"], cwd=worktree).strip())


def base_for_deps(repo: Path | str, task_id: str, deps: list[str],
                  root: Path | str | None = None, default: str = "HEAD") -> str:
    """Base để tạo nhánh task/<task_id> (đề án 0002, bổ sung 2026-11-03):
    - không dep → `default` (HEAD);
    - 1 dep → nhánh task/<dep> (phải tồn tại);
    - nhiều dep → nhánh tích hợp tạm integ/<task_id>: tạo từ task/<dep0>, lần lượt merge
      task/<dep1..n> trong worktree tạm (xoá worktree sau khi xong, giữ nhánh làm base).
      Xung đột → abort, xoá worktree + nhánh tạm, raise MergeConflict.
    Nhánh task/<task_id> đã tồn tại thì caller không cần base (ensure_worktree gắn lại nhánh cũ)."""
    repo = Path(repo).resolve()
    _check_task_id(task_id)
    if not deps:
        return default
    branches = [branch_name(d) for d in deps]
    for br in branches:
        if not branch_exists(repo, br):
            raise RepoError(f"nhánh dep {br} không tồn tại — không tạo được worktree cho {task_id}")
    if len(branches) == 1:
        return branches[0]
    integ = integ_branch_name(task_id)
    _git(["worktree", "prune"], cwd=repo)
    if branch_exists(repo, integ):                     # nhánh tạm từ lần chạy trước → làm lại sạch
        _git(["branch", "-D", "--", integ], cwd=repo)
    wt = (_wt_root(repo, root) / f"_integ-{task_id}").resolve()
    if wt.exists():
        raise RepoError(f"{wt} tồn tại — không ghi đè")
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", integ, "--", _check_path(wt), branches[0]], cwd=repo)
    try:
        for br in branches[1:]:
            try:
                _git(["merge", "-q", "--no-edit", "--no-ff", "-m", f"integ {task_id}: merge {br}",
                      "--end-of-options", br], cwd=wt)
            except RepoError as e:
                files = [f for f in _git(["diff", "--name-only", "--diff-filter=U"], cwd=wt).splitlines()
                         if f]
                try:
                    _git(["merge", "--abort"], cwd=wt)
                except RepoError:
                    pass
                raise MergeConflict(f"xung đột merge deps {branches} cho {task_id} tại {br}: "
                                    f"{files or str(e)}", files, branches) from e
    except BaseException:
        _git(["worktree", "remove", "--force", "--", _check_path(wt)], cwd=repo)
        _git(["branch", "-D", "--", integ], cwd=repo)
        raise
    _git(["worktree", "remove", "--force", "--", _check_path(wt)], cwd=repo)
    return integ


def drop_integ_branch(repo: Path | str, task_id: str) -> None:
    """Xoá nhánh tích hợp tạm (nếu có) sau khi task/<id> đã được tạo từ nó."""
    integ = integ_branch_name(task_id)
    if branch_exists(repo, integ):
        _git(["branch", "-D", "--", integ], cwd=repo)


def list_worktrees(repo: Path | str) -> list[Path]:
    return [Path(e["worktree"]) for e in _worktree_entries(repo) if "worktree" in e]


def list_changed_files(worktree: Path | str, base: str | None = None) -> list[str]:
    """File đổi trong worktree: chưa commit (kể cả untracked) + đã commit so với `base` (nếu có)."""
    files: set[str] = set()
    for line in _git(["status", "--porcelain", "-uall"], cwd=worktree).splitlines():
        path = line[3:]
        files.add(path.split(" -> ", 1)[-1])
    if base is not None:
        rng = f"{_check_ref(base)}...HEAD"
        files.update(f for f in _git(["diff", "--name-only", "--end-of-options", rng, "--"],
                                     cwd=worktree).splitlines() if f)
    return sorted(files)


def diff(worktree: Path | str, base: str = "HEAD") -> str:
    """Diff file đã track (working tree so với `base`). File untracked không có trong diff."""
    return _git(["diff", "--end-of-options", _check_ref(base), "--"], cwd=worktree)


def commit_all(worktree: Path | str, message: str) -> str:
    """Commit cục bộ trên nhánh task. Trả commit sha."""
    _git(["add", "-A"], cwd=worktree)
    _git(["commit", "-q", "-m", message], cwd=worktree)
    return _git(["rev-parse", "HEAD"], cwd=worktree).strip()


def remove_worktree(repo: Path | str, worktree: Path | str, force: bool = False,
                    delete_branch: bool = False, task_id: str | None = None) -> None:
    wt = _check_path(Path(worktree).resolve())
    args = ["worktree", "remove"] + (["--force"] if force else []) + ["--", wt]
    _git(args, cwd=repo)
    _git(["worktree", "prune"], cwd=repo)
    if delete_branch:
        if not task_id:
            raise RepoError("delete_branch cần task_id")
        _git(["branch", "-D" if force else "-d", "--", branch_name(task_id)], cwd=repo)


def cleanup_task_worktrees(repo: Path | str, task_ids: list[str] | None = None,
                           root: Path | str | None = None, force: bool = False,
                           delete_branch: bool = False) -> list[Path]:
    """Dọn worktree task/<id> (mặc định: mọi worktree trên nhánh task/*). Trả list đã xoá.
    Không đụng worktree chính hay worktree không thuộc nhánh task/*."""
    repo = Path(repo).resolve()
    _git(["worktree", "prune"], cwd=repo)
    wanted = None if task_ids is None else {branch_name(t) for t in task_ids}
    removed = []
    for e in _worktree_entries(repo):
        ref = e.get("branch", "")
        if not ref.startswith("refs/heads/task/"):
            continue
        br = ref[len("refs/heads/"):]
        if wanted is not None and br not in wanted:
            continue
        wt = Path(e["worktree"]).resolve()
        if root is not None and wt.parent != Path(root).resolve():
            continue
        remove_worktree(repo, wt, force=force, delete_branch=delete_branch,
                        task_id=br[len("task/"):])
        removed.append(wt)
    return removed
