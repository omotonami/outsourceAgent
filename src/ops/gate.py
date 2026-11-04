"""Gate G1–G4 cho dự án thật (event store) — NGƯỜI ký / trả lại. Code thuần.

Hai đường duy nhất (chủ repo duyệt 2026-11-04 07:4x), cả hai đi qua lõi `sign()` / `reject()` ở đây:
  1. Dashboard: POST /api/projects/{id}/gate/{gate}/{approve|reject} (src/server/app.py) — body có tên người
     ký + typed_confirm "XAC NHAN".
  2. Terminal tương tác do NGƯỜI tự gõ:
       python -m src.ops.gate sign   --id P-... --gate G1 [--edited --note "..."] [--db path]
       python -m src.ops.gate reject --id P-... --gate G1 --reason "..." [--backtrack-to PRESALES] [--db path]
     Bắt buộc stdin là TTY; in tóm tắt + hash dữ liệu trình gate; người gõ tên rồi gõ "XAC NHAN".
     KHÔNG nhận tên người qua tham số dòng lệnh. Không TTY / tên máy / gõ sai / EOF / dữ liệu đổi giữa
     chừng → từ chối, rc≠0, không ghi gì.
Claude/bot KHÔNG chạy lệnh này (CLAUDE.md luật 10; .claude/settings.json deny).

Lõi:
  - Dựng board từ event store (Blackboard.resume → ghi xuyên), kiểm project đang ở đúng AWAIT_Gx.
  - Người ký: confirm.require_human (agent/hệ thống/auto-*/gate_ui → từ chối). Chữ ký ghi qua
    Blackboard.sign(gate, by=<người>, via="gate_ui") — kênh người duy nhất của Blackboard.
  - Ký: guard của transition được thử "như thể đã ký" (không đổi gates.py); guard vẫn từ chối (vd G4 còn
    finding High/Critical) → không ký. Duyệt không sửa → autonomy.record_review cho agent của gate (lỗi
    ghi log → risk_register, chữ ký vẫn giữ, như dashboard demo). Duyệt có sửa → gate_feedback.record_edit.
  - Trả lại: Orchestrator.backtrack (huỷ chữ ký phía sau) + project_status + gate_feedback.record_edit
    (có task_type) cho agent của gate; `project run` chạy lại agent các phase từ chỗ quay về.
"""
from __future__ import annotations

import argparse
import sys

from src.blackboard.store import GATE_UI, Blackboard
from src.common.config import ConfigError
from src.feedback import gate_feedback
from src.governance import confirm
from src.orchestrator import events, gate_view, gates
from src.orchestrator import status as pstatus
from src.orchestrator.autonomy import Autonomy, AutonomyRefused
from src.orchestrator.state_machine import _CFG, ORDER, Orchestrator, TransitionRefused

from . import common

CONFIRM_WORD = "XAC NHAN"
CHANNELS = ("dashboard", "terminal")
WRITER = "orchestrator"          # writer zone trace_log / project_status (Code, không phải danh tính người)
BY = "orchestrator.gate"
REVIEW_LOG_ERRORS = (OSError, ValueError, ConfigError)
REVIEW_RISK_SEVERITY = "Medium"
DECISION_APPROVE, DECISION_REJECT, DECISION_RERUN = "approve", "reject", "rerun_done"


class GateError(Exception):
    """Từ chối ký/trả lại. kind: machine | input | state | hash | guard | missing."""

    def __init__(self, msg: str, kind: str = "input"):
        super().__init__(msg)
        self.kind = kind


# ---------- đọc ----------
def view(store, pid: str) -> dict:
    """Gate đang chờ + dữ liệu trình gate + hash (dùng cho dashboard GET và tóm tắt terminal)."""
    if not common.exists(store, pid):
        raise GateError(f"không có project {pid!r}", "missing")
    b = Blackboard.load(pid, store)
    st = common.state_of(b)
    g = gate_view.STATE_GATE.get(st)
    out = {"project": pid, "state": st, "gate": g,
           "gates_signed": sorted(k for k, v in b._signatures.items() if v.get("valid"))}
    if g and b.is_signed(g):                  # người đã ký, chờ `project run` đi tiếp
        out.update(gate=None, awaiting_run=g, next=f"python -m src.ops.project run --id {pid}")
    elif g:
        p = gate_view.packet(b, g)
        out.update(signer_role=gate_view.GATE_SIGNER[g], packet=p, packet_hash=confirm.payload_hash(p),
                   backtrack_default=gate_view.BACKTRACK[g],
                   backtrack_options=[s for s in gate_view.WORK_STATES if ORDER.index(s) < ORDER.index(st)])
    return out


# ---------- kiểm chung ----------
def _human(signer) -> str:
    return confirm.require_human(signer, lambda m: GateError(m, "machine"),
                                 "người ký {who!r} không phải người (máy/agent/gate_ui không được ký gate)")


def _open(store, pid: str, gate: str, signer, via: str, expected_hash: str | None):
    if via not in CHANNELS:
        raise GateError(f"kênh {via!r} không hợp lệ — chỉ {CHANNELS}")
    if gate not in gate_view.GATE_STATE:
        raise GateError(f"gate lạ: {gate!r}")
    who = _human(signer)
    if not common.exists(store, pid):
        raise GateError(f"không có project {pid!r}", "missing")
    b = Blackboard.resume(pid, store)
    st = common.state_of(b)
    if st != gate_view.GATE_STATE[gate]:
        raise GateError(f"project {pid} đang ở {st}, không chờ {gate}", "state")
    if b.is_signed(gate):
        raise GateError(f"{gate} đã được ký — chạy `python -m src.ops.project run --id {pid}`", "state")
    h = confirm.payload_hash(gate_view.packet(b, gate))
    if expected_hash is not None and expected_hash != h:
        raise GateError("dữ liệu trình gate đã đổi từ lúc người xem — xem lại rồi ký lại", "hash")
    return b, st, who, h


class _AsIfSigned:
    """Board chỉ đọc mà gate `g` coi như đã ký — để thử guard có sẵn trước khi ký (không đổi gates.py)."""

    def __init__(self, board, g):
        self._b, self._g = board, g

    def get(self, zone):
        return self._b.get(zone)

    def is_signed(self, gate):
        return gate == self._g or self._b.is_signed(gate)


def _guard_after_sign(board, state: str, gate: str) -> None:
    t = next(t for t in _CFG["transitions"] if t["from"] == state)
    ok, reason = getattr(gates, t["guard"])(_AsIfSigned(board, gate))
    if not ok:
        raise GateError(f"guard {t['guard']} vẫn từ chối dù có chữ ký: {reason} — không ký", "guard")


def _task_type(ax, gate: str) -> str:
    return (ax.cfg.get("gate_task_types") or {}).get(gate, gate.lower())


def _roles() -> dict:
    from src.orchestrator import room_state
    return {m["id"]: m.get("role") or m["id"] for m in room_state.agent_meta()}


def _feedback(board, gate, signer, field, before, after, task_type, feedback_log):
    roles = _roles()
    for i, a in enumerate(gate_view.GATE_AGENTS[gate]):
        gate_feedback.record_edit(gate, a, roles.get(a, a), field, before, after, reviewer=signer, user_id=signer,
                                  board=board if i == 0 else None, base_log=feedback_log, task_type=task_type)


# ---------- ký ----------
def sign(project_id: str, gate: str, signer, via: str, edited: bool = False, note: str | None = None, *,
         store=None, db=None, expected_hash: str | None = None, autonomy: Autonomy | None = None,
         feedback_log=None) -> dict:
    """NGƯỜI duyệt gate. Raise GateError (không ghi gì) nếu không hợp lệ."""
    store = store or common.open_store(db)
    b, st, who, h = _open(store, project_id, gate, signer, via, expected_hash)
    _guard_after_sign(b, st, gate)
    b.sign(gate, by=who, via=GATE_UI)
    events.emit(b, "DECISION", writer=WRITER, by=BY, gate=gate, decision=DECISION_APPROVE, signer=who,
                channel=via, edited=bool(edited), note=note, packet_hash=h,
                reason=f"{gate} đã duyệt ({via}){' — có sửa' if edited else ''}")
    ax = autonomy or Autonomy()
    tt = _task_type(ax, gate)
    reviews = None
    if edited:
        _feedback(b, gate, who, "gate_approve_edited", h, note or "", tt, feedback_log)
    else:
        try:
            reviews = [ax.record_review(a, tt, gate, reviewer=who, edited=False, approved=True)
                       for a in gate_view.GATE_AGENTS[gate]]
        except (*REVIEW_LOG_ERRORS, AutonomyRefused) as e:
            msg = f"Gate {gate} đã ký nhưng không ghi được review autonomy: {type(e).__name__}: {e}"
            b.append("risk_register", {"risk": msg, "severity": REVIEW_RISK_SEVERITY, "status": "open",
                                       "phase": "autonomy", "gate": gate}, agent=GATE_UI)
    return {"project": project_id, "gate": gate, "decision": DECISION_APPROVE, "signer": who, "channel": via,
            "packet_hash": h, "reviews": len(reviews or []),
            "next": f"python -m src.ops.project run --id {project_id}"}


# ---------- trả lại ----------
def reject(project_id: str, gate: str, signer, via: str, reason: str, backtrack_to: str | None = None, *,
           store=None, db=None, expected_hash: str | None = None, autonomy: Autonomy | None = None,
           feedback_log=None) -> dict:
    """NGƯỜI trả lại gate: backtrack + gate_feedback. Raise GateError (không ghi gì) nếu không hợp lệ."""
    reason = str(reason or "").strip()
    if not reason:
        raise GateError("trả lại cần lý do")
    to = backtrack_to or gate_view.BACKTRACK.get(gate)
    store = store or common.open_store(db)
    b, st, who, h = _open(store, project_id, gate, signer, via, expected_hash)
    if to not in gate_view.WORK_STATES or ORDER.index(to) >= ORDER.index(st):
        raise GateError(f"backtrack_to {to!r} không hợp lệ — chọn phase trước {st}: "
                        f"{[s for s in gate_view.WORK_STATES if ORDER.index(s) < ORDER.index(st)]}")
    orch = Orchestrator(b, st)
    try:
        orch.backtrack(to, reason=reason, by=who)
    except TransitionRefused as e:
        raise GateError(str(e), "state") from e
    pstatus.update(b, orch.state, budget_ratio=common.budget_ratio(store, project_id))
    rerun = [s for s in gate_view.WORK_STATES if ORDER.index(to) <= ORDER.index(s) < ORDER.index(st)]
    events.emit(b, "DECISION", writer=WRITER, by=BY, gate=gate, decision=DECISION_REJECT, signer=who,
                channel=via, backtrack_to=to, rerun=rerun, packet_hash=h,
                reason=f"{gate} trả lại ({via}) → {to}: {reason}")
    ax = autonomy or Autonomy()
    _feedback(b, gate, who, "gate_reject", h, reason, _task_type(ax, gate), feedback_log)
    return {"project": project_id, "gate": gate, "decision": DECISION_REJECT, "signer": who, "channel": via,
            "backtrack_to": to, "rerun": rerun, "packet_hash": h,
            "next": f"python -m src.ops.project run --id {project_id}"}


# ---------- terminal tương tác ----------
def _summary(v: dict) -> str:
    p = v["packet"]
    lines = [f"=== Gate {v['gate']} · project {v['project']} (CLAUDE.md luật 10: chỉ người gõ) ===",
             f"  người ký     : {v['signer_role']}",
             f"  đã ký        : {', '.join(v['gates_signed']) or '(chưa)'}"]
    for k, x in p["highlights"].items():
        if x in (None, [], {}):
            continue
        lines.append(f"  {k:<13}: {x if not isinstance(x, list) else '; '.join(map(str, x))}")
    for z, val in p["zones"].items():
        size = len(val) if isinstance(val, (list, dict)) else (0 if val is None else 1)
        lines.append(f"  zone {z:<8}: {'(trống)' if val is None else f'{size} mục'}")
    lines.append(f"  decision_records: {len(p['decision_records'])}")
    lines.append("  Xem đầy đủ trên dashboard: " + common.DASHBOARD)
    lines.append(f"  packet_hash  : {v['packet_hash']}")
    return "\n".join(lines)


def interactive(action: str, pid: str, gate: str, *, db=None, reason=None, backtrack_to=None, edited=False,
                note=None, stdin=None, out=None, store=None, autonomy=None, feedback_log=None) -> dict:
    """Xác nhận tương tác do chính người gõ rồi gọi lõi. Raise GateError (không ghi gì) nếu từ chối."""
    stdin = stdin if stdin is not None else sys.stdin
    out = out if out is not None else sys.stdout
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        raise GateError("cần terminal tương tác do người gõ (stdin không phải TTY) — bot/pipe/CI không được "
                        "ký/trả lại gate thay người (CLAUDE.md luật 10)", "machine")
    store = store or common.open_store(db)
    v = view(store, pid)
    if v.get("awaiting_run") == gate:
        raise GateError(f"{gate} đã được ký — chạy `{v['next']}`", "state")
    if v["gate"] != gate:
        raise GateError(f"project {pid} đang ở {v['state']}, không chờ {gate}", "state")
    if action == "reject" and not str(reason or "").strip():
        raise GateError("reject cần --reason")
    print(_summary(v), file=out)
    act = "DUYỆT" if action == "sign" else f"TRẢ LẠI → {backtrack_to or v['backtrack_default']}: {reason}"
    print(f"  hành động    : {act}", file=out)

    def ask(prompt):
        print(prompt, end="", file=out, flush=True)
        line = stdin.readline()
        if not line:
            raise GateError("không nhận được xác nhận (EOF) — huỷ, không ghi gì")
        return line.strip()

    who = _human(ask("Tên người ký: "))
    if ask(f'Gõ "{CONFIRM_WORD}" để xác nhận: ') != CONFIRM_WORD:
        raise GateError(f'không gõ đúng "{CONFIRM_WORD}" — huỷ, không ghi gì')
    kw = dict(store=store, expected_hash=v["packet_hash"], autonomy=autonomy, feedback_log=feedback_log)
    if action == "sign":
        return sign(pid, gate, who, "terminal", edited=edited, note=note, **kw)
    return reject(pid, gate, who, "terminal", reason, backtrack_to, **kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.gate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sign", "reject"):
        p = sub.add_parser(name)
        p.add_argument("--id", required=True)
        p.add_argument("--gate", required=True, choices=sorted(gate_view.GATE_STATE))
        p.add_argument("--db", default=None)
        if name == "sign":
            p.add_argument("--edited", action="store_true", help="người đã sửa output trước khi duyệt")
            p.add_argument("--note", default=None)
        else:
            p.add_argument("--reason", required=True)
            p.add_argument("--backtrack-to", default=None, choices=gate_view.WORK_STATES)
    a = ap.parse_args(argv)
    try:
        res = interactive(a.cmd, a.id, a.gate, db=a.db, reason=getattr(a, "reason", None),
                          backtrack_to=getattr(a, "backtrack_to", None), edited=getattr(a, "edited", False),
                          note=getattr(a, "note", None))
    except GateError as e:
        print(f"TỪ CHỐI: {e}", file=sys.stderr)
        return 2
    verb = "ĐÃ DUYỆT" if res["decision"] == DECISION_APPROVE else f"ĐÃ TRẢ LẠI → {res['backtrack_to']}"
    print(f"{verb} {res['gate']} · {res['project']} · người ký: {res['signer']} · {res['packet_hash']}")
    print(f"Tiếp: {res['next']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
