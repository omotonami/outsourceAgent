"""CLI tạo agent mới từ answers đã được người xác nhận.

  python -m src.factory.new --spec spec.yaml            # dry-run: in danh sách file sẽ ghi
  python -m src.factory.new --spec spec.yaml --write    # ghi thật, rồi chạy agent_lint
Chạy trên branch riêng; kết quả đi qua PR + người duyệt (không merge thẳng).
"""
import argparse
import sys
from pathlib import Path

from src.factory import agent_lint, interview, scaffold

ROOT = Path(__file__).parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m src.factory.new")
    ap.add_argument("--spec", required=True, help="YAML answers (xem docs/factory.md)")
    ap.add_argument("--write", action="store_true", help="ghi file (mặc định chỉ dry-run)")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    try:
        spec = interview.build_spec(interview.load_answers(a.spec), root=a.root)
        steps = scaffold.scaffold(spec, root=a.root, dry_run=not a.write)
    except (ValueError, scaffold.ScaffoldError) as e:
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
