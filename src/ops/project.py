"""CLI dự án: new | run | huddle | list | status.  KHÔNG có lệnh ký gate ở đây (người ký qua dashboard hoặc tự gõ `python -m src.ops.gate`).

  python -m src.ops.project new --brief brief.yaml [--id P-...] [--db path]
  python -m src.ops.project run --id P-... [--mock]
  python -m src.ops.project huddle --id P-... --phase presales|discovery [--question "..."] [--mock]
  python -m src.ops.project list
  python -m src.ops.project status --id P-...
Exit code: 0 = chạy xong tới điểm dừng bình thường (gate / released / không có gì làm);
           2 = BLOCKED hoặc ESCALATED (cần người); 1 = lỗi đầu vào.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import ValidationError, validate

from src.blackboard.store import Blackboard
from src.common import config
from src.orchestrator import huddle_trigger
from src.orchestrator import status as pstatus

from . import common, pipeline

BRIEF_SCHEMA = "src/blackboard/schema/lead_brief.json"
_FRONT = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def load_brief(path: str | Path) -> dict:
    """YAML (.yaml/.yml) → dict; Markdown → front matter YAML + thân bài vào `description`."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".md", ".markdown"):
        m = _FRONT.match(text)
        if not m:
            raise ValueError("brief .md cần front matter YAML (--- client/source/received_at ---)")
        data = yaml.safe_load(m.group(1)) or {}
        body = m.group(2).strip()
        if body and "description" not in data:
            data["description"] = body
    else:
        data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("brief phải là object")
    data = json.loads(json.dumps(data, default=str))      # ngày YAML → chuỗi
    validate(data, config.load_json(BRIEF_SCHEMA))
    return data


def cmd_new(a) -> int:
    try:
        brief = load_brief(a.brief)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as e:
        print(f"LỖI brief: {getattr(e, 'message', e)}", file=sys.stderr)
        return 1
    pid = a.id or common.new_id()
    if not _ID.match(pid):
        print(f"LỖI id {pid!r}: chỉ chữ/số/._- (≤64)", file=sys.stderr)
        return 1
    store = common.open_store(a.db)
    if common.exists(store, pid):
        print(f"LỖI: project {pid} đã có trong event store", file=sys.stderr)
        return 1
    b = Blackboard(pid, store=store)
    b.write("lead_brief", brief, agent="intake")
    pstatus.update(b, "INTAKE")
    print(f"Đã tạo project {pid} (state INTAKE) · event store: {store.path}")
    print(f"Tiếp: python -m src.ops.project run --id {pid}   (thêm --mock để chạy thử không cần API key)")
    return 0


def _print_outcome(o: pipeline.Outcome) -> None:
    print(f"[{o.project_id}] state={o.state} · dừng: {o.stop}" + (f" ({o.gate})" if o.gate else ""))
    if o.ran:
        print("  đã chạy: " + ", ".join(o.ran))
    print("CẦN NGƯỜI LÀM:")
    for t in o.human_todo():
        print(f"  - {t}")


def _sender(a):
    if a.mock:
        from . import mock
        return mock.sender
    return None


def cmd_run(a) -> int:
    store = common.open_store(a.db)
    if not common.exists(store, a.id):
        print(f"LỖI: không có project {a.id}", file=sys.stderr)
        return 1
    o = pipeline.run(store, a.id, sender=_sender(a))
    _print_outcome(o)
    return 2 if o.stop in ("BLOCKED", "ESCALATED") else 0


def cmd_huddle(a) -> int:
    from src.agents import huddle
    store = common.open_store(a.db)
    if not common.exists(store, a.id):
        print(f"LỖI: không có project {a.id}", file=sys.stderr)
        return 1
    b = common.resume(store, a.id)
    common.seed_ledger(store, a.id)
    trig = huddle_trigger.check(b, a.phase)        # không truyền autonomy: người gọi tay = manual
    if trig is None and not a.question:
        print(f"Không có điều kiện huddle {a.phase} (hoặc đã huddle rồi). Muốn ép: thêm --question \"...\".")
        return 0
    q = a.question or trig["question"]
    ctx_in = (trig or {}).get("context") or {}
    roles = (huddle.config().get("roles") or {}).get(a.phase) or {}
    who = list(dict.fromkeys(roles.get(r) for r in ("proposer", "critic", "decider") if roles.get(r)))
    for ag in who:                                  # Phòng 3D: thành viên huddle "đang làm"
        pipeline.assign(b, ag, a.phase, a.id)
    try:
        res = huddle.run(b, a.phase, q, ctx_in, {"project_id": a.id}, sender=_sender(a),
                         trigger=(trig or {}).get("trigger") or "manual")
    except BaseException as e:
        for ag in who:
            pipeline.finish(b, ag, a.phase, a.id, exc=e)
        raise
    for ag in who:
        pipeline.finish(b, ag, a.phase, a.id, res)
    if res.get("ok"):
        rec = res["decision_record"]
        print(f"Huddle {a.phase}: chosen={rec.get('chosen')!r} · {rec.get('rounds')} vòng · "
              f"{res.get('tokens_used')} token. decision_record đã ghi vào project "
              f"(RECOMMENDATION cho gate {'G1' if a.phase == 'presales' else 'G2'} — người quyết).")
        return 0
    print(f"Huddle lỗi → cần người: {res.get('error')} {res.get('reason')}")
    return 2


def describe(store, pid: str) -> dict:
    b = Blackboard.load(pid, store)
    st = common.state_of(b)
    tb = b.get("task_backlog") or []
    by: dict[str, int] = {}
    for t in tb:
        by[t.get("status") or "chưa phát"] = by.get(t.get("status") or "chưa phát", 0) + 1
    return {"id": pid, "state": st, "gate_pending": common.pending_gate(st),
            "gates_signed": sorted(g for g, v in b._signatures.items() if v.get("valid")),
            "tasks": dict(sorted(by.items())), "tokens": common.usage_of(store, pid).total}


def cmd_list(a) -> int:
    store = common.open_store(a.db, readonly=True)
    ps = sorted(store.projects(), key=lambda p: p["id"])
    if not ps:
        print("(chưa có project)")
        return 0
    for p in ps:
        d = describe(store, p["id"])
        print(f"{d['id']:<28} {d['state']:<10} gate chờ: {d['gate_pending'] or '-':<3} token: {d['tokens']}")
    return 0


def cmd_status(a) -> int:
    store = common.open_store(a.db, readonly=True)
    if not common.exists(store, a.id):
        print(f"LỖI: không có project {a.id}", file=sys.stderr)
        return 1
    d = describe(store, a.id)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    if d["gate_pending"]:
        g = d["gate_pending"]
        print(f"→ Chờ người ký {g} ({common.GATE_SIGNER[g]}) — dashboard (Gate dự án) hoặc người tự gõ "
              f"`python -m src.ops.gate sign|reject --id {a.id} --gate {g}`.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.project", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    db = argparse.ArgumentParser(add_help=False)
    db.add_argument("--db", default=None, help="SQLite event store (mặc định config/runtime.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", parents=[db], help="tạo project từ lead brief")
    p.add_argument("--brief", required=True)
    p.add_argument("--id", default=None)
    p = sub.add_parser("run", parents=[db], help="chạy tới gate / ESCALATED / BLOCKED")
    p.add_argument("--id", required=True)
    p.add_argument("--mock", action="store_true", help="sender mock, không cần API key")
    p = sub.add_parser("huddle", parents=[db], help="chạy huddle (gọi tay)")
    p.add_argument("--id", required=True)
    p.add_argument("--phase", required=True, choices=["presales", "discovery"])
    p.add_argument("--question", default=None)
    p.add_argument("--mock", action="store_true")
    sub.add_parser("list", parents=[db])
    p = sub.add_parser("status", parents=[db])
    p.add_argument("--id", required=True)
    a = ap.parse_args(argv)
    return {"new": cmd_new, "run": cmd_run, "huddle": cmd_huddle, "list": cmd_list, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
