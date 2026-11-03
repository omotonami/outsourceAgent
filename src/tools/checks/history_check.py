"""CLAUDE.md luật 9: mọi thay đổi phải được ghi vào docs/history/ trong cùng commit/PR.

  python -m src.tools.checks.history_check --base <ref> [--root DIR]

Diff = commit so với `<ref>` (`<ref>...HEAD`) + thay đổi chưa commit (kể cả untracked) — chạy được cả trong
CI lẫn trước khi commit. FAIL (rc 1) nếu:
  - có file đổi trong src/ specs/ config/ mà không có file nào đổi trong docs/history/;
  - có file đợt docs/history/YYYY-MM-DD_<slug>.md chưa có dòng trong mục lục docs/history/README.md.
Lỗi git / ref sai → rc 2. Chỉ kiểm có/không, không kiểm nội dung khớp thay đổi (người review).
"""
import argparse
import re
import sys
from pathlib import Path

from src.tools.integrations import repo as git

ROOT = Path(__file__).parents[3]
GUARDED = ("src/", "specs/", "config/")
HISTORY = "docs/history/"
INDEX = HISTORY + "README.md"
ENTRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9][a-z0-9-]*\.md$")


def check(root=ROOT, base: str = "main") -> list[str]:
    """Trả danh sách lỗi (rỗng = PASS). Raise git.RepoError nếu ref/git lỗi."""
    root = Path(root)
    changed = [f.strip("\"") for f in git.list_changed_files(root, base=base)]
    errs = []
    guarded = [f for f in changed if f.startswith(GUARDED)]
    history = [f for f in changed if f.startswith(HISTORY)]
    if guarded and not history:
        shown = ", ".join(guarded[:5]) + (f" … (+{len(guarded) - 5})" if len(guarded) > 5 else "")
        errs.append(f"có {len(guarded)} file đổi trong src/ specs/ config/ ({shown}) nhưng docs/history/ "
                    f"không đổi — ghi vào file của đợt + dòng mục lục (CLAUDE.md luật 9)")
    hdir = root / HISTORY
    index = (root / INDEX).read_text(encoding="utf-8") if (root / INDEX).is_file() else ""
    if hdir.is_dir():
        if not index:
            errs.append(f"thiếu {INDEX} (quy ước + mục lục)")
        for p in sorted(hdir.glob("*.md")):
            if p.name == "README.md":
                continue
            if not ENTRY_RE.match(p.name):
                errs.append(f"{HISTORY}{p.name}: tên không đúng YYYY-MM-DD_<slug>.md")
            elif f"({p.name})" not in index:
                errs.append(f"{HISTORY}{p.name}: chưa có dòng trong mục lục {INDEX}")
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m src.tools.checks.history_check")
    ap.add_argument("--base", required=True, help="ref so sánh (vd main, b7731d3, origin/main)")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    try:
        errs = check(a.root, a.base)
    except git.RepoError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2
    if errs:
        print("history_check: FAIL\n  " + "\n  ".join(errs))
        return 1
    print(f"history_check: PASS (base {a.base})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
