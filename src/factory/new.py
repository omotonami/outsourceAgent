"""CLI tạo agent mới từ answers đã được người xác nhận.

  python -m src.factory.new --spec spec.yaml --confirmed-by <người>   # dry-run: liệt kê file sẽ ghi
  python -m src.factory.new --spec spec.yaml --write                  # ghi thật: xác nhận TƯƠNG TÁC + agent_lint
  ... --write --create-branch      # tự tạo/chuyển sang nhánh factory/<id> (git cục bộ) trước khi ghi

Dry-run: spec.yaml là answers thô (cần --confirmed-by) hoặc bản ghi interview.confirm(); không ghi gì.

--write (CLAUDE.md luật 10 — bot không tự điền danh tính người):
  - KHÔNG nhận --confirmed-by từ tham số (có → từ chối).
  - Bắt buộc terminal tương tác (sys.stdin.isatty()); không TTY (bot, pipe, CI) → từ chối.
  - Hiện tóm tắt card + hash answers; người gõ tên mình rồi gõ lại "XAC NHAN".
  - spec.yaml là bản ghi đã xác nhận trước đó → kiểm hash, rồi vẫn phải xác nhận lại tương tác
    (confirmed_by trong file có thể do bot điền nên không được tin).
Ghi thật chỉ trên nhánh factory/<id>; kết quả đi qua PR + người duyệt (không merge thẳng, không push).
"""
import argparse
import sys
from pathlib import Path

from src.factory import agent_lint, interview, scaffold
from src.tools.integrations import repo as git

ROOT = Path(__file__).parents[2]
CONFIRM_WORD = "XAC NHAN"


class ConfirmRefused(ValueError):
    """Người không xác nhận / không có terminal tương tác."""


def _answers_for_write(raw: dict) -> dict:
    """answers (chỉ khoá QUESTIONS) từ answers thô / bản nháp LLM / bản ghi đã xác nhận (kiểm hash)."""
    if not isinstance(raw, dict):
        raise ValueError("spec phải là mapping YAML")
    if "answers_hash" in raw:
        a = raw.get("answers")
        if not isinstance(a, dict) or raw["answers_hash"] != interview.answers_hash(a):
            raise ValueError("answers bị sửa sau khi xác nhận (hash không khớp)")
    else:
        a = raw.get("answers", raw) if isinstance(raw.get("answers"), dict) else raw
    return {k: a[k] for k in interview.ANSWER_KEYS if k in a}


def _summary(spec: dict, h: str) -> str:
    lines = [
        "=== Xác nhận tạo agent (CLAUDE.md luật 10: chỉ người gõ) ===",
        f"  id / tên    : {spec['id']} / {spec['name']}",
        f"  job         : {spec['job']}",
        f"  anti_jobs   : {'; '.join(spec['anti_jobs'])}",
        f"  forbidden   : {'; '.join(spec['forbidden']) or '(không)'}",
        f"  done_when   : {'; '.join(spec['done_when'])}",
        f"  input       : {', '.join(f['name'] for f in spec['input'])}",
        f"  output      : {', '.join(f['name'] for f in spec['output'])}",
        f"  zone        : {spec['zone'] or '(không ghi Blackboard)'}",
        f"  tools       : {', '.join(spec['tools']) or '(không)'}",
        f"  phase/risk  : {spec['phase']} / {spec['risk_level']} · model {spec['model_tier']}",
        f"  answers_hash: {h}",
    ]
    return "\n".join(lines)


def interactive_confirm(raw: dict, root=ROOT, stdin=None, out=None) -> dict:
    """Xác nhận tương tác do chính người gõ → bản ghi interview.confirm(). Raise ConfirmRefused/ValueError."""
    stdin = stdin if stdin is not None else sys.stdin
    out = out if out is not None else sys.stdout
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        raise ConfirmRefused("--write cần terminal tương tác do người gõ (stdin không phải TTY) — "
                             "bot/pipe/CI không được xác nhận thay người (CLAUDE.md luật 10)")
    answers = _answers_for_write(raw)
    h = interview.answers_hash(answers)
    spec = interview.preview_spec(answers, root=root)       # kiểm hợp lệ trước khi hỏi người
    print(_summary(spec, h), file=out)

    def ask(prompt):
        print(prompt, end="", file=out, flush=True)
        line = stdin.readline()
        if not line:
            raise ConfirmRefused("không nhận được xác nhận (EOF)")
        return line.strip()

    who = ask("Tên người xác nhận: ")
    if ask(f'Gõ lại "{CONFIRM_WORD}" để xác nhận: ') != CONFIRM_WORD:
        raise ConfirmRefused(f'không gõ đúng "{CONFIRM_WORD}" — huỷ, không ghi gì')
    record = interview.confirm(answers, confirmed_by=who)   # require_human: tên máy → ValueError
    if record["answers_hash"] != h:
        raise ConfirmRefused("hash lệch sau xác nhận — huỷ")
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m src.factory.new")
    ap.add_argument("--spec", required=True, help="YAML answers (xem docs/factory.md)")
    ap.add_argument("--confirmed-by",
                    help="CHỈ cho dry-run. Với --write bị từ chối: xác nhận tương tác (CLAUDE.md luật 10)")
    ap.add_argument("--write", action="store_true",
                    help="ghi file (mặc định chỉ dry-run); cần terminal tương tác để người xác nhận")
    ap.add_argument("--create-branch", action="store_true",
                    help="tạo/chuyển sang nhánh factory/<id> trước khi ghi (git cục bộ)")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    try:
        raw = interview.load_answers(a.spec)
        if a.write:
            if a.confirmed_by:
                raise ConfirmRefused("--confirmed-by không được dùng với --write (CLAUDE.md luật 10): "
                                     "người xác nhận bằng cách gõ trong terminal")
            record = interactive_confirm(raw, root=a.root)
        elif "answers_hash" in raw:
            record = raw
        elif a.confirmed_by:
            record = interview.confirm(raw, confirmed_by=a.confirmed_by)
        else:
            raise ValueError("answers chưa được xác nhận — dry-run: thêm --confirmed-by <người>")
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
    if a.write:
        print(f"  xác nhận: {record['confirmed_by']} · {record['answers_hash']}")
    for s in steps:
        print(f"  {s}")
    if not a.write:
        return 0
    errs = agent_lint.lint(spec["id"], a.root)
    print("agent_lint: PASS" if not errs else "agent_lint: FAIL\n  " + "\n  ".join(errs))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
