"""Review PR #2 (2026-11-04): huddle khi terminal/ESCALATED, resolution giả của agent, room 'working' kẹt,
backtrack khi không rõ phase lỗi, risk escalate theo kind."""
import time

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.ops import escalation, mock, pipeline, project
from src.orchestrator import events, room_state, room_stream, team_feed
from src.orchestrator import status as pstatus
from src.orchestrator.state_machine import Orchestrator
from tests.contract.test_ops_escalation import BRIEF, _failing, _iso, _state, db, pid  # noqa: F401


def _resolve(db, pid, action, who="Lan Nguyen", **kw):
    return escalation.resolve(pid, action, who, "dashboard", store=EventStore(db), **kw)


def _new(db, tmp_path, pid):
    p = tmp_path / f"{pid}.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    assert project.main(["new", "--brief", str(p), "--id", pid, "--db", db]) == 0
    return pid


def _calls():
    calls = []

    def send(model, system, messages, max_tokens):
        calls.append(mock.identify(system))
        return mock.sender(model, system, messages, max_tokens)
    return calls, send


# ---------- 3. huddle chỉ khi phase đang mở ----------
@pytest.mark.parametrize("phase", ["presales", "discovery"])
def test_huddle_refused_when_escalated(db, pid, phase, monkeypatch, capsys):
    calls, send = _calls()
    monkeypatch.setattr(project, "_sender", lambda a: send)
    n = EventStore(db).last_seq(pid)
    rc = project.main(["huddle", "--id", pid, "--phase", phase, "--question", "Q?", "--mock", "--db", db])
    assert rc == 2 and calls == [] and EventStore(db).last_seq(pid) == n
    assert "TỪ CHỐI huddle" in capsys.readouterr().err


@pytest.mark.parametrize("state", ["RELEASED", "ABANDONED"])
def test_huddle_refused_when_terminal(db, tmp_path, state, monkeypatch):
    pid = _new(db, tmp_path, "P-T")
    pstatus.update(Blackboard.resume(pid, EventStore(db)), state)
    calls, send = _calls()
    monkeypatch.setattr(project, "_sender", lambda a: send)
    assert project.main(["huddle", "--id", pid, "--phase", "presales", "--question", "Q?", "--db", db]) == 2
    assert calls == []


def test_huddle_refused_for_phase_not_open(db, tmp_path):
    pid = _new(db, tmp_path, "P-H")
    assert pipeline.run(EventStore(db), pid, sender=mock.sender).gate == "G1"       # AWAIT_G1
    assert project.main(["huddle", "--id", pid, "--phase", "discovery", "--question", "Q?", "--mock",
                         "--db", db]) == 2
    assert project.main(["huddle", "--id", pid, "--phase", "presales", "--question", "Q?", "--mock",
                         "--db", db]) == 0                                             # phase presales đang mở


# ---------- 4. resolution chỉ từ gate_ui + người ----------
def test_agent_appended_resolves_does_not_close_risk(db, pid):
    b = Blackboard.resume(pid, EventStore(db))
    idx = team_feed.open_escalation_index(b.get("risk_register"))
    assert idx is not None
    for forged in ({"resolves": idx, "status": "resolved", "resolved_by": "Lan"},
                   {"resolves": idx, "status": "resolved", "resolved_by": "Lan", "source_agent": "gate_ui"},
                   {"resolves": idx, "status": "resolved", "resolved_by": "a1_bu", "via": "gate_ui"}):
        b.append("risk_register", forged, agent="a1_bu")
    b2 = Blackboard.load(pid, EventStore(db))
    rr = b2.get("risk_register")
    assert all(r["source_agent"] == "a1_bu" for r in rr if "resolves" in r)      # không tự khai source_agent
    assert team_feed.resolved_indexes(rr) == set()
    assert team_feed.open_escalation_index(rr) == idx
    assert escalation.view(EventStore(db), pid)["risk_index"] == idx


def test_gate_ui_resolution_needs_human_resolved_by():
    rr = [{"risk": "x", "status": "open", "source_agent": "orchestrator", "kind": "escalation"},
          {"resolves": 0, "resolved_by": "orchestrator", "source_agent": "gate_ui"},
          {"resolves": 0, "resolved_by": "Claude", "source_agent": "gate_ui"}]
    assert team_feed.resolved_indexes(rr) == set()
    rr.append({"resolves": 0, "resolved_by": "Lan Nguyen", "source_agent": "gate_ui"})
    assert team_feed.resolved_indexes(rr) == {0}
    assert team_feed.open_escalation_index(rr) is None


def test_human_resolve_still_closes(db, pid):
    _resolve(db, pid, "abandon")
    rr = Blackboard.load(pid, EventStore(db)).get("risk_register")
    assert team_feed.open_escalation_index(rr) is None and len(team_feed.resolved_indexes(rr)) == 1


# ---------- 8. room 'working' ----------
def _board_with_assign(ts):
    b = Blackboard("P-ROOM", store=None)
    events.emit(b, "ASSIGN", writer="orchestrator", by="orchestrator", agent="a1_bu", phase="presales",
                clock=lambda: ts)
    return b


def test_room_working_dropped_when_assign_stale_process_died():
    now = time.time()
    b = _board_with_assign(now - 3600)                 # process chết: ASSIGN không bao giờ có RESULT
    assert room_state.step_status(b) == {"a1_bu": "working"}
    assert room_state.step_status(b, now=now, window_s=30) == {}
    a = {x["id"]: x["status"] for x in room_state.snapshot(b, window_s=30)["agents"]}
    assert a["a1_bu"] == "idle"
    fresh = _board_with_assign(now - 1)
    a = {x["id"]: x["status"] for x in room_state.snapshot(fresh, window_s=30)["agents"]}
    assert a["a1_bu"] == "working"


def test_room_window_default_from_config():
    assert room_state.live_window_s() == 30.0


@pytest.mark.parametrize("state", ["AWAIT_G1", "RELEASED", "ESCALATED", "ABANDONED"])
def test_room_working_dropped_at_gate_or_terminal(state):
    b = _board_with_assign(time.time())
    assert room_state.step_status(b, state=state) == {}
    assert room_state.step_status(b, state="PRESALES") == {"a1_bu": "working"}


def test_room_blocked_kept_when_terminal(db, pid):
    feed = room_stream.RoomFeed(EventStore(db), pid)
    a = {x["id"]: x["status"] for x in feed.snapshot()["agents"]}
    assert a["a1_bu"] == "blocked"


# ---------- 9. backtrack khi không rõ phase lỗi ----------
def _legacy_unknown_phase(db, tmp_path):
    """Dự án cũ: ESCALATED không có DECISION, reason không rõ agent, không có state trước trong log."""
    pid = _new(db, tmp_path, "P-OLD2")
    b = Blackboard.resume(pid, EventStore(db))
    o = Orchestrator(b, "INTAKE")
    o.escalate("lỗi không rõ")
    pstatus.update(b, o.state)
    return pid


def test_legacy_unknown_phase_backtrack_security_refused(db, tmp_path):
    pid = _legacy_unknown_phase(db, tmp_path)
    v = escalation.view(EventStore(db), pid)
    assert v["from_state"] is None
    assert v["options"] == ["backtrack:PRESALES", "abandon"]
    n = EventStore(db).last_seq(pid)
    for to in ("SECURITY", "BUILD", "DISCOVERY"):
        with pytest.raises(escalation.EscalationError) as e:
            _resolve(db, pid, f"backtrack:{to}")
        assert e.value.kind == "state"
    assert EventStore(db).last_seq(pid) == n and _state(db, pid) == "ESCALATED"
    assert _resolve(db, pid, "backtrack:PRESALES")["state"] == "PRESALES"


# ---------- 10. risk escalate theo kind ----------
def test_escalate_marks_kind_and_scanner_risk_not_picked(db, pid):
    b = Blackboard.resume(pid, EventStore(db))
    rr = b.get("risk_register")
    idx = team_feed.open_escalation_index(rr)
    assert rr[idx]["kind"] == "escalation"
    b.append("risk_register", {"risk": "Scanner bảo mật còn stub", "severity": "High", "status": "open",
                               "phase": "security"}, agent="orchestrator")
    v = escalation.view(EventStore(db), pid)
    assert v["risk_index"] == idx and "SCHEMA_FAIL" in v["reason"]


def test_legacy_risk_without_kind_still_found():
    rr = [{"risk": "a1_bu: SCHEMA_FAIL", "source_agent": "orchestrator", "severity": "High", "status": "open"},
          {"risk": "scanner", "source_agent": "orchestrator", "phase": "security", "status": "open"},
          {"risk": "khác", "source_agent": "orchestrator", "kind": "other", "status": "open"}]
    assert team_feed.open_escalation_index(rr) == 0
