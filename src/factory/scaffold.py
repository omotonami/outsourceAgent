"""Scaffold (Code thuần, KHÔNG LLM): spec dict -> đủ bộ file cho một agent mới.

Sinh/ghi:
  create specs/02_agents/<id>.md · specs/04_schemas/<id>.in.json / .out.json · src/agents/<id>.py
         knowledge/<role>/team/SKILL.md · knowledge/<role>/personal/_template/SKILL.md
         tests/contract/test_<id>.py
  append specs/03_routing.yaml (agents) · specs/05_blackboard.yaml (zones)
         (runtime đọc thẳng 2 file này — không còn bản sao trong src/, quyết định 2026-11-03)
Mặc định dry_run=True: chỉ trả danh sách việc sẽ làm. Không ghi đè file/entry đã có (raise ScaffoldError).
YAML được chèn dạng text (giữ comment), sau đó parse lại để chắc không phá file (yaml_edit.py).
Nội dung file dựng ở templates.py; module này lo kế hoạch, kiểm tra, ghi + rollback.
Ghi thật (dry_run=False) CHỈ trên nhánh git `factory/<id>` (output là branch/PR, người duyệt);
lỗi giữa chừng → rollback mọi file đã ghi.
zone=None: agent không ghi Blackboard (vd factory_interviewer) → không sinh entry zone.
"""
import json
from pathlib import Path

from src.common import config
from src.factory.templates import (render_personal_skill, render_runner, render_schema, render_spec_md,
                                   render_team_skill, render_test)
from src.factory.yaml_edit import ScaffoldError, insert_in_block, routing_line, yaml_append, zone_line

ROOT = config.ROOT

ROUTING_FILES = ["specs/03_routing.yaml"]
ZONE_FILES = ["specs/05_blackboard.yaml"]
__all__ = ["ScaffoldError", "plan", "scaffold", "factory_branch", "require_factory_branch", "insert_in_block",
           "routing_line", "zone_line", "render_spec_md", "render_runner", "render_test", "render_team_skill",
           "render_personal_skill", "render_schema"]


# ---------- plan / apply ----------
def plan(spec, root=ROOT):
    """Trả list {action, path, content}. Raise ScaffoldError nếu sẽ đè thứ đã có."""
    root = Path(root)
    aid, role = spec["id"], spec["role"]
    creates = {
        f"specs/02_agents/{aid}.md": render_spec_md(spec),
        f"specs/04_schemas/{aid}.in.json": json.dumps(render_schema(spec, "in"), ensure_ascii=False, indent=2) + "\n",
        f"specs/04_schemas/{aid}.out.json": json.dumps(render_schema(spec, "out"), ensure_ascii=False, indent=2) + "\n",
        f"src/agents/{aid}.py": render_runner(spec),
        f"knowledge/{role}/team/SKILL.md": render_team_skill(spec),
        f"knowledge/{role}/personal/_template/SKILL.md": render_personal_skill(spec),
        f"tests/contract/test_{aid}.py": render_test(spec),
    }
    exists = [p for p in creates if (root / p).exists()]
    if exists:
        raise ScaffoldError(f"file đã tồn tại — không ghi đè: {exists}")
    # role phải duy nhất (skill_loader tìm spec theo hậu tố id)
    for p in (root / "specs/02_agents").glob("*.md"):
        if p.stem.split("_", 1)[-1] == role:
            raise ScaffoldError(f"role '{role}' đã có spec {p.name}")
    for sub in spec.get("subordinates", []):
        if not (root / f"specs/02_agents/{sub}.md").exists():
            raise ScaffoldError(f"subordinate '{sub}' không có spec")

    steps = [{"action": "create", "path": p, "content": c} for p, c in creates.items()]
    zone_steps = [(r, "zones", spec["zone"], zone_line(spec)) for r in ZONE_FILES] if spec.get("zone") else []
    for rel, block, key, line in (
            [(r, "agents", aid, routing_line(spec)) for r in ROUTING_FILES] + zone_steps):
        f = root / rel
        if not f.exists():
            raise ScaffoldError(f"thiếu {rel}")
        new = yaml_append(f.read_text(encoding="utf-8"), block, key, line, rel)
        steps.append({"action": "append", "path": rel, "content": new, "line": line})
    return steps


def factory_branch(agent_id):
    return f"factory/{agent_id}"


def require_factory_branch(root, agent_id):
    """Ghi thật chỉ trên nhánh factory/<id> (không ghi thẳng nhánh chính)."""
    from src.tools.integrations import repo as git
    want = factory_branch(agent_id)
    try:
        cur = git.current_branch(root)
    except git.RepoError as e:
        raise ScaffoldError(f"{root} không phải git repo — cần nhánh {want}: {e}") from e
    if cur != want:
        raise ScaffoldError(f"đang ở nhánh {cur!r}; chỉ ghi trên nhánh {want!r} "
                            f"(dùng --create-branch hoặc git switch -c {want})")


def _write_step(root, s):
    p = root / s["path"]
    if s["action"] == "create":
        with p.open("x", encoding="utf-8") as fh:   # 'x' = không bao giờ đè
            fh.write(s["content"])
    else:
        p.write_text(s["content"], encoding="utf-8")


def _rollback(root, journal, new_dirs):
    errs = []
    for path, before in reversed(journal):
        try:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(before, encoding="utf-8")
        except OSError as e:
            errs.append(f"{path}: {e}")
    for d in sorted(new_dirs, key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    return errs


def scaffold(spec, root=ROOT, dry_run=True):
    """dry_run=True: trả ['create <path>', 'append <path>: <line>'...], không ghi gì.
    dry_run=False: kiểm tra hết (kể cả nhánh factory/<id>) rồi mới ghi; lỗi giữa chừng → rollback."""
    steps = plan(spec, root)
    summary = [f"{s['action']} {s['path']}" + (f": {s['line']}" if s.get("line") else "") for s in steps]
    if dry_run:
        return summary
    root = Path(root)
    require_factory_branch(root, spec["id"])
    journal, new_dirs = [], []
    try:
        for s in steps:
            p = root / s["path"]
            missing = [d for d in [p.parent, *p.parent.parents] if not d.exists()]
            p.parent.mkdir(parents=True, exist_ok=True)
            new_dirs.extend(missing)
            if s["action"] == "create":
                if p.exists():
                    raise ScaffoldError(f"{s['path']} vừa xuất hiện — không ghi đè")
                try:
                    _write_step(root, s)
                except FileExistsError:
                    raise                            # file của người khác: không đưa vào journal
                except BaseException:
                    if p.exists():
                        journal.append((p, None))
                    raise
                journal.append((p, None))
            else:
                journal.append((p, p.read_text(encoding="utf-8")))
                _write_step(root, s)
    except Exception as e:
        errs = _rollback(root, journal, new_dirs)
        msg = f"ghi dở ở {s['path']}: {e} — đã rollback {len(journal)} file"
        raise ScaffoldError(msg + (f"; rollback lỗi: {errs}" if errs else "")) from e
    return summary
