"""Phòng 3D: snapshot chỉ đọc (agent/task/event có from-to), chỉ summary; endpoint /api/room + trang /room."""
import json
import re

from src.blackboard.store import Blackboard
from src.orchestrator import room_state, team_feed
from src.server import app as srv

D = "orchestrator.dispatcher"


def _board():
    b = Blackboard("p")
    b.write("task_backlog", [
        {"id": "T1", "spec": "KYC onboarding " * 20, "risk_level": "high", "owns": ["src/kyc/"], "status": "in_dev"},
        {"id": "T2", "spec": "Remit FX", "risk_level": "normal", "deps": ["T1"], "status": "testing"},
        {"id": "T3", "spec": "Admin", "risk_level": "low", "status": "blocked", "blocked_reason": "over_budget"},
        {"id": "T4", "spec": "Notif", "risk_level": "low", "status": "await_merge"},
        {"id": "T5", "spec": "Report", "risk_level": "low"},
    ], agent="b4_tech_lead")
    tr = lambda e: b.append("trace_log", e, agent="orchestrator")
    tr({"event": "ASSIGN", "task_id": "T1", "ts": 1.0, "by": D, "agent": "c1_dev"})
    tr({"event": "SPAWN", "task_id": "T1", "ts": 1.1, "by": D, "trigger": "ready", "attempt": 1,
        "thinking": "RAW-THINKING", "contract": {"goal": "RAW-CONTRACT"}})
    tr({"event": "RESULT", "task_id": "T1", "ts": 2.0, "by": D, "ok": False, "reason": "QC fail"})
    tr({"event": "KILL", "task_id": "T1", "ts": 2.5, "by": D, "reason": "quá ttl"})
    tr({"event": "BLOCKED", "task_id": "T3", "ts": 3.0, "by": D, "reason": "over_budget"})
    tr({"event": "llm_call", "ts": 0.1, "by": "gateway", "messages": "RAW-PROMPT"})
    b.append("huddle_log", {"round": 1, "agent": "b1_ba", "role": "proposer", "summary": "KYC 4 bước",
                            "ts": 4.0}, agent="b1_ba")
    b.append("decision_records", {"question": "Flow KYC?", "options": ["A"], "chosen": "A", "rationale": "x",
                                  "risks": [], "open_questions": [], "participants": ["b1_ba"], "rounds": 1,
                                  "ts": 5.0}, agent="huddle")
    b.append("risk_register", {"risk": "FX chưa rõ", "severity": "High", "status": "open"}, agent="a1_bu")
    return b


def test_agents_all_specs_with_phase_and_status():
    snap = room_state.snapshot(_board())
    ids = {a["id"] for a in snap["agents"]}
    assert len(ids) == 14 and {"a1_bu", "c1_dev", "e2_cos", "factory_interviewer"} <= ids
    by = {a["id"]: a for a in snap["agents"]}
    assert by["a1_bu"]["phase"] == "A" and by["b4_tech_lead"]["phase"] == "B" and by["d1_soc"]["phase"] == "D"
    assert by["factory_interviewer"]["phase"] == "E" and by["c1_dev"]["name"] == "C1 Dev"
    assert all(a["status"] in room_state.STATUSES for a in snap["agents"])
    assert by["c1_dev"]["status"] == "blocked" and set(by["c1_dev"]["tasks"]) == {"T1", "T3"}
    assert by["c1_dev"]["current_task"] == "T1"                                  # ưu tiên task không bị chặn
    assert by["c2_tester"]["status"] == "working" and by["c2_tester"]["current_task"] == "T2"
    assert by["a1_bu"]["status"] == "blocked"                                    # risk High còn mở
    assert by["b2_ux"]["status"] == "idle" and by["b2_ux"]["current_task"] is None


def test_active_gate_and_levels():
    snap = room_state.snapshot(Blackboard("p"), active=["b1_ba"], gate={"id": "G3", "agents": ["c3_qc"]},
                               levels={"c3_qc": "L1"})
    by = {a["id"]: a for a in snap["agents"]}
    assert by["b1_ba"]["status"] == "working" and by["c3_qc"]["status"] == "waiting_gate"
    assert by["c3_qc"]["level"] == "L1" and by["a1_bu"]["level"] is None
    assert snap["gate"] == {"id": "G3", "agents": ["c3_qc"]}


def test_tasks_owner_and_title():
    ts = {t["id"]: t for t in room_state.tasks(_board())}
    assert [t for t in ts] == ["T1", "T2", "T3", "T4", "T5"]
    assert ts["T1"]["owner_agent"] == "c1_dev" and ts["T2"]["owner_agent"] == "c2_tester"
    assert ts["T3"]["owner_agent"] == "c1_dev" and ts["T4"]["owner_agent"] is None
    assert ts["T5"]["status"] == "ready" and ts["T2"]["deps"] == ["T1"] and ts["T1"]["owns"] == ["src/kyc/"]
    assert len(ts["T1"]["title"]) <= room_state.TITLE_MAX


def test_events_from_to_and_summary_reused():
    evs = room_state.events(_board())
    assert {e["type"] for e in evs} <= set(team_feed.TYPES)
    assert [e["summary"] for e in evs] == [e["summary"] for e in team_feed.events(_board())]
    by = {e["id"]: e for e in evs}
    assert by["trace_log:0"]["from"] == D and by["trace_log:0"]["to"] == "c1_dev"            # ASSIGN
    sp = by["trace_log:1"]
    assert sp["type"] == "SPAWN" and sp["to"] == "c1_dev" and sp["parent"] == D and sp["agent"] == "c1_dev"
    assert by["trace_log:2"]["from"] == "c1_dev" and by["trace_log:2"]["to"] == D             # RESULT
    assert by["trace_log:3"]["type"] == "KILL" and by["trace_log:3"]["to"] == "c1_dev"
    assert by["huddle_log:0"]["from"] == "b1_ba" and by["huddle_log:0"]["to"] == room_state.HUDDLE
    assert by["decision_records:0"]["from"] == room_state.HUDDLE
    assert by["risk_register:0"]["type"] == "BLOCKED" and by["risk_register:0"]["from"] == "a1_bu"
    assert "trace_log:5" not in by                                                         # llm_call bỏ qua
    keys = {"id", "ts", "type", "from", "to", "summary", "task_id", "parent", "agent", "source"}
    assert all(set(e) == keys for e in evs)


def test_summary_only_read_only_and_limit():
    b = _board()
    before = json.dumps(b._data, sort_keys=True, default=str)
    snap = room_state.snapshot(b, limit=3)
    out = json.dumps(snap, ensure_ascii=False)
    for raw in ("RAW-THINKING", "RAW-CONTRACT", "RAW-PROMPT", "blocked_reason"):
        assert raw not in out
    assert len(snap["events"]) == 3 and snap["events"][-1]["id"] == "risk_register:0"
    assert json.dumps(b._data, sort_keys=True, default=str) == before


def test_empty_board():
    snap = room_state.snapshot(Blackboard("p"))
    assert snap["tasks"] == [] and snap["events"] == [] and snap["gate"] is None
    assert all(a["status"] == "idle" for a in snap["agents"])


def _route(path):
    return next(r for r in srv.app.routes if getattr(r, "path", None) == path)


def test_api_room_endpoint(monkeypatch):
    """Không có httpx → gọi thẳng handler của route GET /api/room."""
    route = _route("/api/room")
    assert "GET" in route.methods
    monkeypatch.setattr(srv.S, "board", _board())
    monkeypatch.setattr(srv, "_room_levels", lambda: {"c1_dev": "L0"})
    out = route.endpoint(limit=100)
    assert {"agents", "tasks", "events", "phases", "gate"} <= set(out)
    assert "RAW-THINKING" not in json.dumps(out, ensure_ascii=False)
    assert len(route.endpoint(limit=0)["events"]) == 1                               # limit kẹp ≥ 1
    by = {a["id"]: a for a in out["agents"]}
    assert by["c1_dev"]["level"] == "L0"


def test_api_room_marks_current_step_and_gate(monkeypatch):
    route = _route("/api/room")
    monkeypatch.setattr(srv, "_room_levels", lambda: {})
    monkeypatch.setattr(srv.S, "board", Blackboard("p"))
    monkeypatch.setattr(srv.S, "i", next(j for j, s in enumerate(srv.S.steps) if s["k"] == "b1"))
    by = {a["id"]: a for a in route.endpoint(limit=10)["agents"]}
    assert by["b1_ba"]["status"] == "working"
    g3 = next(j for j, s in enumerate(srv.S.steps) if s.get("gate") == "G3")
    monkeypatch.setattr(srv.S, "i", g3)
    out = route.endpoint(limit=10)
    assert out["gate"]["id"] == "G3" and "c1_dev" in out["gate"]["agents"]
    assert {a["id"]: a for a in out["agents"]}["c1_dev"]["status"] == "waiting_gate"


def test_room_page_and_dashboard_link():
    html = _route("/room").endpoint()
    assert "<canvas" in html or "WebGLRenderer" in html
    assert re.search(r'"three": "https://cdn\.jsdelivr\.net/npm/three@\d+\.\d+\.\d+/build/three\.module\.js"', html)
    assert "/api/room" in html and "demo" in html and "prefers-reduced-motion" in html
    assert "max-width:760px" in html and "Trao đổi" in html and "DEMO" in html
    idx = (srv.HERE / "static/index.html").read_text(encoding="utf-8")
    assert "Phòng 3D" in idx and "/room" in idx
