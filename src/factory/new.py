"""CLI tạo agent mới từ answers đã được người xác nhận.

  python -m src.factory.new --spec spec.yaml --confirmed-by <người>            # dry-run
  python -m src.factory.new --spec spec.yaml --confirmed-by <người> --write    # ghi thật + agent_lint
  ... --write --create-branch      # tự tạo/chuyển sang nhánh factory/<id> (git cục bộ) trước khi ghi
spec.yaml là answers thô (cần --confirmed-by: người chạy CLI xác nhận) hoặc bản ghi của
interview.confirm() (có confirmed_by + answers_hash). Ghi thật chỉ trên nhánh factory/<id>;
kết quả đi qua PR + người duyệt (không merge thẳng, không push).
"""
import argparse
import sys
from pathlib import Path

from src.factory import agent_lint, interview, scaffold
from src.tools.integrations import repo as git

ROOT = Path(__file__).parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m src.factory.new")
    ap.add_argument("--spec", required=True, help="YAML answers (xem docs/factory.md)")
    ap.add_argument("--confirmed-by", help="người xác nhận answers (bắt buộc nếu spec là answers thô)")
    ap.add_argument("--write", action="store_true", help="ghi file (mặc định chỉ dry-run)")
    ap.add_argument("--create-branch", action="store_true",
                    help="tạo/chuyển sang nhánh factory/<id> trước khi ghi (git cục bộ)")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    try:
        raw = interview.load_answers(a.spec)
        if "answers_hash" in raw:
            record = raw
        elif a.confirmed_by:
            record = interview.confirm(raw, confirmed_by=a.confirmed_by)
        else:
            raise ValueError("answers chưa được xác nhận — thêm --confirmed-by <người>")
        spec = interview.build_spec(record, root=a.root)
        if a.write and a.create_branch:
            br = scaffold.factory_branch(spec["id"])
            if git.current_branch(a.root) != br:
                git.switch_branch(a.root, br, create=not git.branch_exists(a.root, br))
        steps = scaffold.scaffold(spec, root=a.root, dry_run=not a.write)
    except (ValueError, scaffold.ScaffoldError, git.RepoError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2
    print(("ĐÃ GHI" if a.write else "DRY-RUN (thêm --write để ghi)") + f" — agent {spec['id']}:")
    for s in steps:
        print(f"  {s}")
    if not a.write:
        return 0
    errs = agent_lint.lint(spec["id"], a.root)
    print("agent_lint: PASS" if not errs else "agent_lint: FAIL\n  " + "\n  ".join(errs))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
