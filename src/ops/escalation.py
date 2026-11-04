"""Gỡ ESCALATED cho dự án thật (event store) — chỉ NGƯỜI quyết. Code thuần. Cùng chuẩn với ký gate (luật 10).

Hai đường duy nhất, cả hai đi qua lõi `resolve()` ở đây:
  1. Dashboard: POST /api/projects/{id}/escalation/{action} (src/server/app.py) — body: signer (tên người),
     typed_confirm "XAC NHAN", note, packet_hash (khuyên gửi). GET /api/projects/{id}/escalation để xem.
  2. Terminal tương tác do NGƯỜI tự gõ:
       python -m src.ops.escalation resolve --id P-... --action retry|backtrack:DISCOVERY|abandon [--note "..."]
     Bắt buộc stdin là TTY; in tóm tắt + hash; người gõ tên rồi gõ "XAC NHAN". KHÔNG nhận tên người qua tham số.
Claude/bot KHÔNG chạy lệnh/API này (.claude/settings.json deny).

Hành động:
  retry               state về phase lỗi (escalated_from), agent lỗi chạy lại ở `project run` kế tiếp với signal
                      `retry_2` → router nâng tier lên `escalate_to` nếu specs/03_routing.yaml cho phép.
  backtrack:<STATE>   STATE ∈ PRESALES|DISCOVERY|BUILD|SECURITY: Orchestrator.backtrack (huỷ chữ ký gate phía
                      sau STATE) + chạy lại các phase từ STATE tới phase lỗi (như gate bị trả lại).
  abandon             đóng dự án: state ABANDONED (terminal, không transition ra). Dữ liệu giữ nguyên.
Mọi hành động: DECISION trong trace_log (by orchestrator.escalation, signer = người) + bản ghi resolution APPEND
vào risk_register (zone append-only: {resolves: <chỉ số risk escalate>, status: resolved, resolved_by: người,
via: gate_ui}) — consumer coi risk được trỏ tới là đã đóng (team_feed.open_risks).
"""
from __future__ import annotations

import argparse
import sys

from src.blackboard.store import GATE_UI, Blackboard
from src.governance import confirm
from src.orchestrator import events, gate_view, team_feed
from src.orchestrator import status as pstatus
from src.orchestrator.state_machine import ORDER, Orchestrator, TransitionRefused

from . import common, pipeline

CONFIRM_WORD = "XAC NHAN"
CHANNELS = ("dashboard", "terminal")
WRITER = "orchestrator"                 # writer zone trace_log / project_status (Code, không phải danh tính người)
BY = pipeline.BY_ESCALATION
ESCALATED, ABANDONED = "ESCALATED", "ABANDONED"
RETRY_SIGNALS = ("retry_2",)            # specs/03_routing.yaml escalate_on → tier escalate_to nếu agent có
WORK = gate_view.WORK_STATES
PHASE_STATE = {"a": "PRESALES", "b": "DISCOVERY", "c": "BUILD", "d": "SECURITY"}


class EscalationError(Exception):
    """Từ chối gỡ. kind: machine | input | state | hash | missing (cùng mã HTTP như GateError)."""

    def __init__(self, msg: str, kind: str = "input"):
        super().__init__(msg)
        self.kind = kind


# ---------- đọc ----------
def _escalation(store, pid: str, b) -> dict:
    """{reason, risk_index, from_state, agent} của lần escalate đang mở."""
    rr = b.get("risk_register") or []
    done = team_feed.resolved_indexes(rr)
    idx = next((i for i in range(len(rr) - 1, -1, -1)
                if rr[i].get("source_agent") == "orchestrator" and team_feed.is_open_risk(rr[i], i in done)), None)
    dec = None
    for e in b.get("trace_log") or []:
        if e.get("event") == "DECISION" and e.get("by") == pipeline.BY and e.get("decision") == "escalate":
            dec = e
    frm, agent = (dec or {}).get("escalated_from"), (dec or {}).get("agent")
    if frm is None:                     # dự án escalate trước bản này: state cuối trước ESCALATED trong log
        for r in store.read(pid):
            st = (r.payload or {}).get("state") if r.zone == "project_status" and isinstance(r.payload, dict) else None
            if st and st != ESCALATED:
                frm = st
    reason = rr[idx]["risk"] if idx is not None else (dec or {}).get("reason")
    if agent is None and reason:        # "a1_bu: SCHEMA_FAIL — ..." (định dạng pipeline._check)
        head = str(reason).split(":", 1)[0].strip()
        agent = head if head in _agent_ids() else None
    if frm not in WORK and agent and agent[:1] in PHASE_STATE and agent[1:2].isdigit():
        frm = PHASE_STATE[agent[:1]]
    return {"reason": reason, "risk_index": idx, "from_state": frm if frm in WORK else None, "agent": agent}


def _agent_ids() -> set[str]:
    from src.common import config
    return set(config.agent_ids()) | {"dispatcher"}


def _options(esc: dict) -> list[str]:
    frm = esc["from_state"]
    back = [s for s in WORK if frm is None or ORDER.index(s) <= ORDER.index(frm)]
    return (["retry"] if frm else []) + [f"backtrack:{s}" for s in back] + ["abandon"]


def _packet(pid: str, state: str, esc: dict) -> dict:
    return {"project": pid, "state": state, **esc}


def view(store, pid: str) -> dict:
    """Tình trạng ESCALATED + lựa chọn + hash (dashboard GET và tóm tắt terminal)."""
    if not common.exists(store, pid):
        raise EscalationError(f"không có project {pid!r}", "missing")
    b = Blackboard.load(pid, store)
    st = common.state_of(b)
    out = {"project": pid, "state": st, "escalated": st == ESCALATED}
    if st != ESCALATED:
        return out
    esc = _escalation(store, pid, b)
    return {**out, **esc, "options": _options(esc), "packet_hash": confirm.payload_hash(_packet(pid, st, esc))}


# ---------- lõi ----------
def _human(signer) -> str:
    return confirm.require_human(signer, lambda m: EscalationError(m, "machine"),
                                 "người gỡ {who!r} không phải người (máy/agent/gate_ui không được gỡ ESCALATED)")


def parse_action(action) -> tuple[str, str | None]:
    a = str(action or "").strip()
    if a in ("retry", "abandon"):
        return a, None
    if a.startswith("backtrack:"):
        to = a.split(":", 1)[1].strip().upper()
        if to not in WORK:
            raise EscalationError(f"backtrack tới {to!r} không hợp lệ — chọn {list(WORK)}")
        return "backtrack", to
    raise EscalationError(f"action {a!r} không hợp lệ — retry | backtrack:<{'|'.join(WORK)}> | abandon")


def resolve(project_id: str, action: str, signer, via: str, note: str | None = None, *, store=None, db=None,
            expected_hash: str | None = None) -> dict:
    """NGƯỜI gỡ ESCALATED. Raise EscalationError (không ghi gì) nếu không hợp lệ."""
    if via not in CHANNELS:
        raise EscalationError(f"kênh {via!r} không hợp lệ — chỉ {CHANNELS}")
    kind, to = parse_action(action)
    who = _human(signer)
    note = (str(note).strip() or None) if note is not None else None
    store = store or common.open_store(db)
    if not common.exists(store, project_id):
        raise EscalationError(f"không có project {project_id!r}", "missing")
    b = Blackboard.resume(project_id, store)
    st = common.state_of(b)
    if st != ESCALATED:
        raise EscalationError(f"project {project_id} đang ở {st}, không ESCALATED", "state")
    esc = _escalation(store, project_id, b)
    h = confirm.payload_hash(_packet(project_id, st, esc))
    if expected_hash is not None and expected_hash != h:
        raise EscalationError("tình trạng ESCALATED đã đổi từ lúc người xem — xem lại rồi làm lại", "hash")
    frm = esc["from_state"]
    fields: dict = {}
    if kind == "retry":
        if not frm:
            raise EscalationError("không xác định được phase lỗi — dùng backtrack:<PHASE> hoặc abandon", "state")
        new = frm
        fields = {"agent": esc["agent"], "signals": list(RETRY_SIGNALS), "retry_state": frm}
    elif kind == "backtrack":
        if frm and ORDER.index(to) > ORDER.index(frm):
            raise EscalationError(f"backtrack chỉ về phase ≤ phase lỗi {frm}: {_options(esc)}")
        orch = Orchestrator(b, ESCALATED)
        try:
            orch.backtrack(to, reason=f"gỡ ESCALATED → {to}" + (f": {note}" if note else ""), by=who)
        except TransitionRefused as e:
            raise EscalationError(str(e), "state") from e
        new = orch.state
        last = frm or to
        fields = {"backtrack_to": to, "rerun": [s for s in WORK if ORDER.index(to) <= ORDER.index(s) <= ORDER.index(last)]}
    else:
        new = ABANDONED
    label = action if kind != "backtrack" else f"backtrack:{to}"
    events.emit(b, "DECISION", writer=WRITER, by=BY, decision=kind, signer=who, channel=via, note=note,
                escalated_from=frm, packet_hash=h, **fields,
                reason=f"người gỡ ESCALATED ({via}): {label}" + (f" — {note}" if note else ""))
    if esc["risk_index"] is not None:
        orig = (b.get("risk_register") or [])[esc["risk_index"]]
        b.append("risk_register", {"risk": f"Đã gỡ ESCALATED ({label}): {orig.get('risk')}",
                                   "severity": orig.get("severity") or "High", "status": "resolved",
                                   "resolves": esc["risk_index"], "resolution": label, "resolved_by": who,
                                   "via": GATE_UI, "channel": via, "note": note, "phase": "escalation"},
                 agent=GATE_UI)
    pstatus.update(b, new, budget_ratio=common.budget_ratio(store, project_id))
    nxt = (f"python -m src.ops.project run --id {project_id}" if new != ABANDONED else None)
    return {"project": project_id, "action": label, "state": new, "signer": who, "channel": via,
            "packet_hash": h, **fields, "next": nxt}


# ---------- terminal tương tác ----------
def _summary(v: dict, action: str) -> str:
    return "\n".join([
        f"=== Gỡ ESCALATED · project {v['project']} (CLAUDE.md luật 10: chỉ người gõ) ===",
        f"  lý do        : {v.get('reason') or '(không rõ)'}",
        f"  phase lỗi    : {v.get('from_state') or '(không rõ)'} · agent: {v.get('agent') or '(không rõ)'}",
        f"  lựa chọn     : {', '.join(v.get('options') or [])}",
        f"  hành động    : {action}",
        f"  packet_hash  : {v.get('packet_hash')}",
        "  Xem chi tiết: python -m src.ops.report daily --id " + v["project"]])


def interactive(pid: str, action: str, *, db=None, note=None, stdin=None, out=None, store=None) -> dict:
    stdin = stdin if stdin is not None else sys.stdin
    out = out if out is not None else sys.stdout
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        raise EscalationError("cần terminal tương tác do người gõ (stdin không phải TTY) — bot/pipe/CI không được "
                              "gỡ ESCALATED thay người (CLAUDE.md luật 10)", "machine")
    parse_action(action)
    store = store or common.open_store(db)
    v = view(store, pid)
    if not v["escalated"]:
        raise EscalationError(f"project {pid} đang ở {v['state']}, không ESCALATED", "state")
    print(_summary(v, action), file=out)

    def ask(prompt):
        print(prompt, end="", file=out, flush=True)
        line = stdin.readline()
        if not line:
            raise EscalationError("không nhận được xác nhận (EOF) — huỷ, không ghi gì")
        return line.strip()

    who = _human(ask("Tên người quyết: "))
    if ask(f'Gõ "{CONFIRM_WORD}" để xác nhận: ') != CONFIRM_WORD:
        raise EscalationError(f'không gõ đúng "{CONFIRM_WORD}" — huỷ, không ghi gì')
    return resolve(pid, action, who, "terminal", note, store=store, expected_hash=v["packet_hash"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.escalation", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("resolve")
    p.add_argument("--id", required=True)
    p.add_argument("--action", required=True, help="retry | backtrack:PRESALES|DISCOVERY|BUILD|SECURITY | abandon")
    p.add_argument("--note", default=None)
    p.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    try:
        res = interactive(a.id, a.action, db=a.db, note=a.note)
    except EscalationError as e:
        print(f"TỪ CHỐI: {e}", file=sys.stderr)
        return 2
    print(f"ĐÃ GỠ ESCALATED · {res['project']} · {res['action']} → {res['state']} · người quyết: {res['signer']}")
    if res["next"]:
        print(f"Tiếp: {res['next']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
