"""Lỗi thực tế B (2026-11-04): Phòng 3D không thấy agent nào 'đang làm' ở presales/discovery vì pipeline không
phát sự kiện theo agent. Pipeline phải ghi ASSIGN trước mỗi lần gọi agent, RESULT/BLOCKED sau; room_state suy
working/idle/blocked từ cặp đó."""
import json

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.gateway import trace
from src.ops import escalation, gate, mock, pipeline, project
from src.orchestrator import autonomy, room_state, room_stream, team_feed
from src.orchestrator.autonomy import Autonomy

BRIEF = "client: Acme\nsource: email\nreceived_at: 2026-11-01\ndomain: fintech\nsummary: Ví nội bộ\n"
PRESALES = ["a1_bu", "a2_estimation", "a3_proposal"]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(autonomy, "STATE", tmp_path / "autonomy_state.json")
    monkeypatch.delenv("OA_EVENTSTORE_PATH", raising=False)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ev.sqlite3")


@pytest.fixture
def pid(db, tmp_path):
    p = tmp_path / "brief.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    assert project.main(["new", "--brief", str(p), "--id", "P-R", "--db", db]) == 0
    return "P-R"


def _steps(db, pid):
    """(event, agent) của bước phase (task_id rỗng) theo thứ tự trong event store."""
    out = []
    for r in EventStore(db).read(pid):
        e = r.payload or {}
        if r.zone == "trace_log" and e.get("event") in ("ASSIGN", "RESULT", "BLOCKED") \
                and not e.get("task_id") and e.get("agent"):
            out.append((e["event"], e["agent"]))
    return out


def _status(db, pid):
    snap = room_stream.RoomFeed(EventStore(db, readonly=True), pid).snapshot()
    return {a["id"]: a["status"] for a in snap["agents"]}, snap


def test_mock_presales_emits_assign_result_in_order(db, pid):
    o = pipeline.run(EventStore(db), pid, sender=mock.sender)
    assert o.gate == "G1"
    assert _steps(db, pid) == [(k, a) for a in PRESALES for k in ("ASSIGN", "RESULT")]
    tl = Blackboard.load(pid, EventStore(db)).get("trace_log")
    a = next(e for e in tl if e.get("event") == "ASSIGN" and e.get("agent") == "a1_bu")
    assert a["from"] == "orchestrator" and a["to"] == "a1_bu" and a["phase"] == "presales" and a["project"] == pid
    r = next(e for e in tl if e.get("event") == "RESULT" and e.get("agent") == "a1_bu")
    assert r["ok"] is True and r["from"] == "a1_bu" and r["to"] == "orchestrator"
    st, _ = _status(db, pid)
    assert all(st[x] != "working" for x in st)                       # dừng ở gate: không ai 'đang làm'
    assert st["a1_bu"] == "waiting_gate"


def test_snapshot_mid_run_shows_working_agent(db, pid):
    seen = {}

    def send(model, system, messages, max_tokens):
        who = mock.identify(system)
        if who == "a2_estimation" and "mid" not in seen:
            seen["mid"] = _status(db, pid)                          # process khác đọc cùng event store
        return mock.sender(model, system, messages, max_tokens)
    pipeline.run(EventStore(db), pid, sender=send)
    st, snap = seen["mid"]
    assert st["a2_estimation"] == "working" and st["a1_bu"] == "idle" and st["a3_proposal"] == "idle"
    assert sum(1 for s in st.values() if s == "working") == 1
    ev = [e for e in snap["events"] if e["type"] == "ASSIGN"][-1]
    assert ev["from"] == "orchestrator" and ev["to"] == "a2_estimation"
    assert ev["summary"] == "giao bước presales cho a2_estimation"
    res = [e for e in snap["events"] if e["type"] == "RESULT"][-1]
    assert res["from"] == "a1_bu" and res["summary"] == "a1_bu: OK"


def test_discovery_steps_after_g1(db, pid, tmp_path):
    pipeline.run(EventStore(db), pid, sender=mock.sender)
    ax = Autonomy(state_path=tmp_path / "ax.json", reviews_log=tmp_path / "rv.jsonl")
    gate.sign(pid, "G1", "Lan", "dashboard", store=EventStore(db), autonomy=ax, feedback_log=tmp_path / "fb.jsonl")
    assert pipeline.run(EventStore(db), pid, sender=mock.sender).gate == "G2"
    disc = _steps(db, pid)[6:]
    assert disc == [(k, a) for a in ("b1_ba", "b2_ux", "b3_ui", "b4_tech_lead") for k in ("ASSIGN", "RESULT")]
    tl = Blackboard.load(pid, EventStore(db)).get("trace_log")
    assert {e["phase"] for e in tl if e.get("event") == "ASSIGN" and e.get("agent", "").startswith("b")} == {"discovery"}


def _bad_a1(model, system, messages, max_tokens):
    if mock.identify(system) == "a1_bu":
        return "RAW-SECRET văn bản " + json.dumps({"domain": "x"}), {"total_tokens": 1}
    return mock.sender(model, system, messages, max_tokens)


def test_failed_agent_blocked_without_raw_output_then_human_retry_clears(db, pid):
    assert pipeline.run(EventStore(db), pid, sender=_bad_a1).stop == "ESCALATED"
    assert _steps(db, pid) == [("ASSIGN", "a1_bu"), ("BLOCKED", "a1_bu")]
    st, snap = _status(db, pid)
    assert st["a1_bu"] == "blocked"
    blk = [e for e in snap["events"] if e["type"] == "BLOCKED" and e.get("agent") == "a1_bu"][-1]
    assert "SCHEMA_FAIL" in blk["summary"] and "RAW-SECRET" not in blk["summary"]
    escalation.resolve(pid, "retry", "Lan Nguyen", "dashboard", store=EventStore(db))
    assert _status(db, pid)[0]["a1_bu"] == "idle"                    # người gỡ → hết blocked
    assert pipeline.run(EventStore(db), pid, sender=_bad_a1).stop == "ESCALATED"
    assert _status(db, pid)[0]["a1_bu"] == "blocked"                 # retry_done không xoá blocked mới


def test_exception_in_agent_call_emits_blocked(db, pid):
    def boom(model, system, messages, max_tokens):
        raise RuntimeError("mạng rớt")
    o = pipeline.run(EventStore(db), pid, sender=boom)
    assert o.stop == "BLOCKED" and "mạng rớt" in o.reason
    assert _steps(db, pid) == [("ASSIGN", "a1_bu"), ("BLOCKED", "a1_bu")]
    assert _status(db, pid)[0]["a1_bu"] == "blocked"


def test_manual_huddle_emits_assign_result(db, pid, capsys):
    pipeline.run(EventStore(db), pid, sender=mock.sender)
    n = len(_steps(db, pid))
    rc = project.main(["huddle", "--id", pid, "--phase", "presales", "--question", "Chọn cổng thanh toán?",
                       "--mock", "--db", db])
    assert rc == 0
    assert _steps(db, pid)[n:] == [("ASSIGN", "a1_bu"), ("ASSIGN", "a2_estimation"),
                                   ("RESULT", "a1_bu"), ("RESULT", "a2_estimation")]


def test_step_status_unit():
    tl = [{"event": "ASSIGN", "agent": "a1_bu"}, {"event": "RESULT", "agent": "a1_bu", "ok": True},
          {"event": "ASSIGN", "agent": "a2_estimation"},
          {"event": "ASSIGN", "agent": "c1_dev", "task_id": "T1"},          # Dispatcher: theo task, không ở đây
          {"event": "ASSIGN", "agent": "b1_ba"}, {"event": "BLOCKED", "agent": "b1_ba", "reason": "x"}]

    class B:
        def get(self, z):
            return tl if z == "trace_log" else None
    assert room_state.step_status(B()) == {"a2_estimation": "working", "b1_ba": "blocked"}
    tl.append({"event": "DECISION", "by": "orchestrator.escalation", "decision": "retry_done"})
    assert room_state.step_status(B())["b1_ba"] == "blocked"
    tl.append({"event": "DECISION", "by": "orchestrator.escalation", "decision": "abandon"})
    assert "b1_ba" not in room_state.step_status(B())


def test_team_feed_summaries_for_phase_steps():
    evs = team_feed._trace_events([{"event": "ASSIGN", "agent": "a1_bu", "phase": "presales"},
                                   {"event": "RESULT", "agent": "a1_bu", "ok": True},
                                   {"event": "BLOCKED", "agent": "a1_bu", "reason": "a1_bu: SCHEMA_FAIL: x"}])
    assert [e["summary"] for e in evs] == ["giao bước presales cho a1_bu", "a1_bu: OK", "a1_bu: SCHEMA_FAIL: x"]
