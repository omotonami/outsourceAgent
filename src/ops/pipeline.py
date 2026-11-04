"""Chạy dự án từ state hiện tại tới điểm cần NGƯỜI (gate / ESCALATED / BLOCKED). Code thuần.

- State dựng lại từ event store (Blackboard.resume + project_status.state) → chạy tiếp qua nhiều
  process / nhiều ngày. Mỗi bước idempotent: zone đã có thì không gọi lại agent đó.
- Agent gọi qua runner trong src/agents/ (→ gateway). BUILD dùng Dispatcher.
- Mỗi lần gọi agent ở presales/discovery/security (và huddle gọi tay, src/ops/project.py): trace_log ghi
  ASSIGN {from: orchestrator, to: agent, agent, phase, project} TRƯỚC khi gọi, rồi RESULT (ok) hoặc BLOCKED
  (lỗi / exception) với summary ngắn — không raw output. Phòng 3D (room_state) suy working/idle/blocked từ cặp
  này (task_id rỗng = bước phase; BUILD giữ sự kiện theo task của Dispatcher). Huddle KHÔNG chạy ở đây
  (chỉ khi người gọi `project huddle`, quyết định chủ repo).
- Đa dự án (src/ops/scheduler.py): `slots.hold(roles, pid, "main")` bao mỗi lần gọi agent (BUILD: cả C1–C3) →
  trần việc chính/role + số lượt chạy song song (config/scheduler.yaml). Không truyền slots → không giới hạn.
- Gate: chỉ ĐỌC chữ ký (board.is_signed). Module này không có đường ký gate (luật 10, specs/06_gates.md);
  người ký/trả lại qua src/ops/gate.py (dashboard hoặc terminal tương tác do người gõ).
- Gate bị trả lại (trace_log DECISION decision=reject, rerun=[phase...]): chạy lại agent của các phase đó dù
  zone đã có; xong phase → ghi DECISION decision=rerun_done.
- Escalate: agent fail schema (escalate=True) hoặc vượt budget → Orchestrator.escalate → ESCALATED; ghi
  DECISION decision=escalate {escalated_from, agent} để người gỡ (src/ops/escalation.py) biết chạy lại từ đâu.
- Người gỡ ESCALATED (src/ops/escalation.resolve, chỉ người — luật 10): retry → state về phase lỗi, agent lỗi
  chạy lại với signal `retry_2` (router nâng tier nếu routing cho phép); backtrack → như gate bị trả lại
  (DECISION decision=backtrack, rerun=[...]); abandon → ABANDONED (đóng, không chạy nữa).
- BLOCKED: guard từ chối / Dispatcher còn task blocked / lỗi môi trường → dừng, ghi sự kiện BLOCKED vào
  trace_log (writer orchestrator), state giữ nguyên để chạy lại sau khi người xử lý.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from src.gateway import budget as ledger
from src.orchestrator import events, team_feed
from src.orchestrator import status as pstatus
from src.orchestrator.dispatcher import Dispatcher, default_pipeline
from src.orchestrator.state_machine import Orchestrator, TransitionRefused

from . import common

USER = "chief-ops"          # user_id cho skill_loader (không phải danh tính người)
WRITER = "orchestrator"
BY = "orchestrator.ops"
BY_GATE = "orchestrator.gate"             # DECISION reject/rerun_done (src/ops/gate.py)
BY_ESCALATION = "orchestrator.escalation"  # DECISION retry/backtrack/abandon do người gỡ (src/ops/escalation.py)
RERUN_DECISIONS = {BY_GATE: "reject", BY_ESCALATION: "backtrack"}


class Escalate(Exception):
    def __init__(self, msg: str, agent: str | None = None):
        super().__init__(msg)
        self.agent = agent


class Blocked(Exception):
    pass


@dataclass
class Outcome:
    project_id: str
    state: str
    stop: str                      # AWAIT_GATE | ESCALATED | BLOCKED | RELEASED | ABANDONED
    gate: str | None = None
    reason: str | None = None
    ran: list[str] = field(default_factory=list)

    def human_todo(self) -> list[str]:
        if self.stop == "AWAIT_GATE":
            return [f"Người ({common.GATE_SIGNER[self.gate]}) xem và duyệt/trả lại {self.gate}: dashboard "
                    f"({common.DASHBOARD}, khối 'Gate dự án') hoặc TỰ GÕ trong terminal của mình: "
                    f"`python -m src.ops.gate sign|reject --id {self.project_id} --gate {self.gate}`. "
                    "Claude/bot KHÔNG ký thay, không chạy lệnh ký.",
                    f"Ký xong: chạy lại `python -m src.ops.project run --id {self.project_id}`."]
        if self.stop == "ESCALATED":
            return [f"Dự án ESCALATED: {self.reason}.",
                    "NGƯỜI quyết cách gỡ (retry | backtrack:<PHASE> | abandon): dashboard POST "
                    f"/api/projects/{self.project_id}/escalation/<action> hoặc TỰ GÕ trong terminal của mình: "
                    f"`python -m src.ops.escalation resolve --id {self.project_id} --action retry` "
                    "(hoặc backtrack:PRESALES / abandon). Claude/bot KHÔNG gỡ thay, không chạy lệnh này.",
                    f"Gỡ xong (retry/backtrack): chạy lại `python -m src.ops.project run --id {self.project_id}`."]
        if self.stop == "ABANDONED":
            return ["Dự án đã ĐÓNG (ABANDONED) do người quyết — không còn bước tự động."]
        if self.stop == "BLOCKED":
            return [f"BLOCKED: {self.reason}. Người xử lý nguyên nhân rồi chạy lại `run`."]
        return ["RELEASED — không còn bước tự động."]


SUMMARY_REASON_MAX = 160


def assign(board, agent: str, phase: str, pid: str, kind: str = "main") -> dict:
    """ASSIGN cho một bước phase (task_id rỗng) — Phòng 3D thấy agent 'đang làm'. kind: main (bước pipeline)
    | side (việc phụ, src/ops/side_tasks.py) — scheduler đa dự án (src/ops/scheduler.py)."""
    return events.emit(board, "ASSIGN", writer=WRITER, by=WRITER, agent=agent, phase=phase, project=pid,
                       kind=kind, **{"from": WRITER, "to": agent})


class NoSlots:
    """Không giới hạn (chạy 1 dự án bằng `project run`). Scheduler truyền Slots thật (src/ops/scheduler.py)."""

    @contextlib.contextmanager
    def hold(self, roles, pid, kind="main"):
        yield


BUILD_ROLES = ("c1_dev", "c2_tester", "c3_qc")      # BUILD: Dispatcher giữ cả 3 role trong suốt lượt chạy


def finish(board, agent: str, phase: str, pid: str, res: dict | None = None,
           exc: BaseException | None = None) -> dict:
    """RESULT (ok) / BLOCKED (lỗi) sau bước phase. Chỉ mã lỗi + lý do cắt ngắn (runtime không đưa raw output
    vào reason); exception → tên lớp + thông điệp cắt ngắn."""
    if exc is None and res is not None and res.get("ok"):
        return events.emit(board, "RESULT", writer=WRITER, by=WRITER, agent=agent, phase=phase, project=pid,
                           ok=True, **{"from": agent, "to": WRITER})
    if exc is not None:
        why = f"{type(exc).__name__}: {exc}"
    else:
        why = f"{(res or {}).get('error') or 'lỗi'}: {(res or {}).get('reason') or ''}"
    why = " ".join(why.split())
    why = why if len(why) <= SUMMARY_REASON_MAX else why[: SUMMARY_REASON_MAX - 1] + "…"
    return events.emit(board, "BLOCKED", writer=WRITER, by=WRITER, agent=agent, phase=phase, project=pid,
                       reason=f"{agent}: {why}", **{"from": agent, "to": WRITER})


def _check(res: dict, agent: str) -> dict:
    if res.get("ok"):
        return res
    msg = f"{agent}: {res.get('error')} — {res.get('reason')}"
    if res.get("escalate"):
        raise Escalate(msg, agent)
    raise Blocked(msg)


class Runner:
    def __init__(self, store, pid: str, sender=None, slots=None):
        self.store, self.pid, self.sender = store, pid, sender
        self.slots = slots or NoSlots()
        self.board = common.resume(store, pid)
        common.seed_ledger(store, pid)
        self.orch = Orchestrator(self.board, common.state_of(self.board))
        self.ran: list[str] = []
        self.current: str | None = None        # agent đang được gọi (để ghi escalate đúng agent)
        self.force = self._pending_rerun()
        self.retry = self._pending_retry()

    # ---------- tiện ích ----------
    def _kw(self, agent: str | None = None) -> dict:
        kw = {"sender": self.sender} if self.sender else {}
        if agent and self.retry and self.retry.get("agent") == agent and self.retry.get("signals"):
            kw["signals"] = list(self.retry["signals"])
        return kw

    def _ctx(self, **extra) -> dict:
        return {"project_id": self.pid, **extra}

    def _status(self) -> None:
        pstatus.update(self.board, self.orch.state, budget_ratio=common.budget_ratio(self.store, self.pid))

    def _advance(self) -> None:
        try:
            self.orch.advance()
        except TransitionRefused as e:
            raise Blocked(f"guard từ chối ở {self.orch.state}: {e}") from e
        self._status()

    def _pending_rerun(self) -> set[str]:
        """Phase phải chạy lại vì gate bị người trả lại (src/ops/gate.reject), chưa có rerun_done."""
        todo: set[str] = set()
        for e in self.board.get("trace_log") or []:
            if e.get("event") != "DECISION" or e.get("by") not in RERUN_DECISIONS:
                continue
            if e.get("decision") == RERUN_DECISIONS[e["by"]]:
                todo |= set(e.get("rerun") or [])
            elif e.get("decision") == "rerun_done":
                todo.discard(e.get("phase_state"))
        return todo

    def _pending_retry(self) -> dict | None:
        """Người gỡ ESCALATED bằng retry (src/ops/escalation) mà agent đó chưa chạy lại (chưa có retry_done)."""
        cur = None
        for e in self.board.get("trace_log") or []:
            if e.get("event") != "DECISION" or e.get("by") != BY_ESCALATION:
                continue
            if e.get("decision") == "retry":
                cur = {"agent": e.get("agent"), "signals": e.get("signals") or []}
            elif e.get("decision") in ("retry_done", "backtrack", "abandon"):
                cur = None
        return cur

    def _call(self, agent: str, fn) -> dict:
        """Gọi runner của agent (fn nhận kw) — nơi duy nhất gọi agent ở presales/discovery/security."""
        self.current = agent
        kw = self._kw(agent)
        phase = self.PHASES.get(self.orch.state, self.orch.state.lower())
        try:
            with self.slots.hold((agent,), self.pid, "main"):     # RESULT/BLOCKED ghi TRƯỚC khi nhả role
                assign(self.board, agent, phase, self.pid)
                try:
                    res = fn(kw)
                except BaseException as e:
                    finish(self.board, agent, phase, self.pid, exc=e)
                    raise
                finish(self.board, agent, phase, self.pid, res)
                return res
        finally:
            if "signals" in kw:
                events.emit(self.board, "DECISION", writer=WRITER, by=BY_ESCALATION, decision="retry_done",
                            agent=agent, reason=f"đã chạy lại {agent} sau khi người gỡ ESCALATED (retry)")
                self.retry = None

    def _fresh(self, zone: str) -> bool:
        return self.board.get(zone) is not None and self.orch.state not in self.force

    def _agent(self, zone: str, agent: str, fn) -> None:
        if self._fresh(zone):
            return
        _check(self._call(agent, fn), agent)
        self.ran.append(agent)

    # ---------- phase ----------
    def presales(self) -> None:
        from src.agents import a1_bu, a2_estimation, a3_proposal
        b = self.board
        self._agent("business_context", "a1_bu", lambda kw: a1_bu.run(
            b, USER, self._ctx(), {"project_id": self.pid, "lead_brief": b.get("lead_brief")}, **kw))
        bc = b.get("business_context")
        mods = [{"name": m["name"], **({"desc": m["desc"]} if m.get("desc") else {})} for m in bc.get("modules", [])]
        rc = common.rate_card()
        ctx = self._ctx(domain=bc.get("domain", "default"), blended_rate=rc.get("blended_rate", 0),
                        tech_stack=bc.get("tech_hints", []))
        self._agent("estimate", "a2_estimation", lambda kw: a2_estimation.run(
            b, USER, ctx, {"project_id": self.pid, "business_context": bc, "modules": mods}, **kw))
        self._agent("proposal", "a3_proposal", lambda kw: a3_proposal.run(
            b, USER, self._ctx(), {"business_context": bc, "estimate": b.get("estimate")}, **kw))

    def discovery(self) -> None:
        from src.agents import b1_ba, b2_ux, b3_ui, b4_tech_lead
        b = self.board
        self._agent("requirements", "b1_ba", lambda kw: b1_ba.run(
            b, USER, self._ctx(), {"signed_proposal": b.get("proposal"),
                                   "business_context": b.get("business_context")}, **kw))
        stories = b.get("requirements")["stories"]
        self._agent("design_spec", "b2_ux", lambda kw: b2_ux.run(b, USER, self._ctx(), {"stories": stories}, **kw))
        self._agent("ui_system", "b3_ui", lambda kw: b3_ui.run(
            b, USER, self._ctx(), {"design_spec": b.get("design_spec"), "stories": stories}, **kw))
        if not self._fresh("task_backlog"):
            res = _check(self._call("b4_tech_lead", lambda kw: b4_tech_lead.run(b, USER, self._ctx(), {
                "stories": stories, "screens": (b.get("design_spec") or {}).get("screens", [])}, **kw)),
                "b4_tech_lead")
            # runner b4 không tự ghi: Orchestrator ghi hộ ĐÚNG danh tính writer zone (như dry_run)
            b.write("architecture", {"architecture": res["data"]["architecture"],
                                     "adr": res["data"].get("adr", [])}, agent="b4_tech_lead")
            b.write("task_backlog", res["data"]["task_backlog"], agent="b4_tech_lead")
            self.ran.append("b4_tech_lead")

    def build(self) -> None:
        self.current = "dispatcher"
        worker = default_pipeline(user_id=USER, sender=self.sender)
        with self.slots.hold(BUILD_ROLES, self.pid, "main"):
            res = Dispatcher(self.board, worker=worker).run()
        self.ran.append("dispatcher")
        if res.get("escalation") and "budget" in str(res["escalation"]):
            raise Escalate(f"Dispatcher: {res['escalation']}", "dispatcher")
        if res.get("blocked"):
            raise Blocked(f"task blocked: {sorted(res['blocked'])}")
        if res.get("escalation"):
            raise Blocked(f"Dispatcher: {res['escalation']}")

    def security(self) -> None:
        from src.agents import d1_soc
        from src.tools.security import dependency_scan, sast, secret_scan
        b = self.board
        if self._fresh("security_report"):
            return
        raw, missing = [], []
        for name, tool in (("sast", sast), ("dependency_scan", dependency_scan), ("secret_scan", secret_scan)):
            try:
                raw += tool.run(".")
            except NotImplementedError:
                missing.append(name)
        if missing:   # không giả vờ đã quét: người thấy ở report + trước G4
            b.append("risk_register", {"risk": f"Scanner bảo mật còn stub, CHƯA quét thật: {missing}",
                                       "severity": "High", "status": "open", "phase": "security"}, agent=WRITER)
        self._agent("security_report", "d1_soc", lambda kw: d1_soc.run(
            b, USER, self._ctx(), {"raw_findings": raw}, **kw))

    PHASES = {"PRESALES": "presales", "DISCOVERY": "discovery", "BUILD": "build", "SECURITY": "security"}

    # ---------- vòng chạy ----------
    def run(self) -> Outcome:
        try:
            while True:
                s = self.orch.state
                if s in ("RELEASED", "ABANDONED"):
                    return self._out(s)
                if s == "ESCALATED":
                    return self._out("ESCALATED", reason=self._last_escalation())
                gate = common.pending_gate(s)
                if gate:
                    if not self.board.is_signed(gate):
                        return self._out("AWAIT_GATE", gate=gate)
                    self._advance()
                    continue
                if s == "INTAKE":
                    if self.board.get("project_status") is None:
                        self._status()
                    self._advance()
                    continue
                getattr(self, self.PHASES[s])()
                if s in self.force:
                    events.emit(self.board, "DECISION", writer=WRITER, by=BY_GATE,
                                decision="rerun_done", phase_state=s, reason=f"đã chạy lại {s} sau khi gate bị trả lại")
                    self.force.discard(s)
                self._advance()
        except (Escalate, ledger.BudgetExceeded) as e:
            reason = str(e) if isinstance(e, Escalate) else f"vượt budget: {e}"
            frm = self.orch.state
            agent = getattr(e, "agent", None) or self.current
            events.emit(self.board, "DECISION", writer=WRITER, by=BY, decision="escalate", escalated_from=frm,
                        agent=agent, reason=reason)
            self.orch.escalate(reason)
            self._status()
            return self._out("ESCALATED", reason=reason)
        except Blocked as e:
            return self._blocked(str(e))
        except Exception as e:  # noqa: BLE001 — lỗi môi trường (thiếu key, mạng...): dừng, không đổi state
            return self._blocked(f"lỗi khi chạy: {type(e).__name__}: {e}")

    def _blocked(self, reason: str) -> Outcome:
        events.emit(self.board, "BLOCKED", writer=WRITER, by=BY, reason=reason, state=self.orch.state)
        return self._out("BLOCKED", reason=reason)

    def _last_escalation(self) -> str | None:
        rr = [r for r in team_feed.open_risks(self.board.get("risk_register"))
              if r.get("source_agent") == "orchestrator"]
        return rr[-1]["risk"] if rr else None

    def _out(self, stop: str, gate: str | None = None, reason: str | None = None) -> Outcome:
        return Outcome(self.pid, self.orch.state, stop, gate=gate, reason=reason, ran=list(self.ran))


def run(store, pid: str, sender=None, slots=None) -> Outcome:
    return Runner(store, pid, sender, slots).run()
