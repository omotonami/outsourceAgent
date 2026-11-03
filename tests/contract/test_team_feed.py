"""H5 Team feed: timeline chuẩn hoá + cây spawn từ Blackboard; chỉ summary; endpoint /api/feed."""
import json

from src.blackboard.store import Blackboard
from src.orchestrator import team_feed
from src.server import app as srv

D = "orchestrator.dispatcher"


def _board():
    b = Blackboard("p")
    tr = lambda e: b.append("trace_log", e, agent="orchestrator")
    tr({"event": "SPAWN", "task_id": "T1", "ts": 1.0, "by": D, "trigger": "ready", "attempt": 1,
        "thinking": "RAW-THINKING", "contract": {"goal": "RAW-CONTRACT"}})
    tr({"event": "SPAWN", "task_id": "T2", "ts": 2.0, "by": D, "attempt": 1})
    tr({"event": "SPAWN", "task_id": "T2a", "ts": 2.5, "by": "orchestrator", "parent": "T2"})
    tr({"event": "RESULT", "task_id": "T1", "ts": 3.0, "by": D, "ok": True, "attempt": 1})
    tr({"event": "KILL", "task_id": "T2", "ts": 4.0, "by": D, "reason": "quá ttl 1800s"})
    tr({"event": "BLOCKED", "task_id": None, "ts": 5.0, "by": D, "reason": "over_budget"})
    tr({"event": "ASSIGN", "task_id": "T3", "ts": 0.5, "by": D, "agent": "c1_dev"})
    tr({"event": "llm_call", "ts": 0.1, "by": "gateway"})                       # không phải sự kiện team
    b.append("huddle_log", {"round": 1, "agent": "a1_bu", "role": "proposer", "summary": "Đề xuất A"},
             agent="a1_bu")
    b.append("decision_records", {"question": "Chọn FX?", "options": ["A", "B"], "chosen": "A",
             "rationale": "rẻ", "risks": [], "open_questions": ["Hạn mức?"], "participants": ["a1_bu"],
             "rounds": 1}, agent="huddle")
    b.append("risk_register", {"risk": "FX chưa rõ", "severity": "High", "status": "open"}, agent="a1_bu")
    b.append("risk_register", {"risk": "nhỏ", "severity": "Low", "status": "open"}, agent="a1_bu")
    b.append("risk_register", {"risk": "đã xong", "severity": "Critical", "status": "closed"}, agent="a1_bu")
    return b


def test_types_normalized_and_sorted():
    evs = team_feed.events(_board())
    assert {e["type"] for e in evs} == set(team_feed.TYPES)
    assert all(e["type"] in team_feed.TYPES for e in evs)
    timed = [e["ts"] for e in evs if e["ts"] is not None]
    assert timed == sorted(timed)
    assert evs[0]["type"] == "ASSIGN"                                            # ts 0.5 trước SPAWN 1.0
    srcs = {e["source"] for e in evs}
    assert srcs == {"trace_log", "huddle_log", "decision_records", "risk_register"}
    blocked = [e["summary"] for e in evs if e["source"] == "risk_register"]
    assert blocked == ["risk High: FX chưa rõ"]                                   # bỏ Low và closed
    dec = next(e for e in evs if e["type"] == "DECISION")
    assert "Chọn FX? → A" in dec["summary"] and "1 câu hỏi mở" in dec["summary"]


def test_summary_only_no_raw():
    out = json.dumps(team_feed.build(_board()), ensure_ascii=False)
    assert "RAW-THINKING" not in out and "RAW-CONTRACT" not in out
    for e in team_feed.events(_board()):
        assert set(e) == {"type", "ts", "actor", "task_id", "summary", "source"}
        assert len(e["summary"]) <= team_feed.MAX_SUMMARY
    b = Blackboard("p")
    b.append("huddle_log", {"round": 1, "agent": "b1_ba", "role": "critic", "summary": "x" * 5000}, agent="b1_ba")
    assert len(team_feed.events(b)[0]["summary"]) == team_feed.MAX_SUMMARY


def test_spawn_tree_parent_child_and_status():
    tree = team_feed.spawn_tree(_board())
    assert [n["id"] for n in tree] == [D]
    kids = {c["id"]: c for c in tree[0]["children"]}
    assert kids["T1"]["status"] == "OK" and kids["T2"]["status"] == "KILL"
    assert [c["id"] for c in kids["T2"]["children"]] == ["T2a"]                  # cây nhiều tầng


def test_feed_is_read_only_and_limit():
    b = _board()
    before = json.dumps(b._data, sort_keys=True, default=str)
    out = team_feed.build(b, limit=3)
    assert len(out["events"]) == 3 and out["counts"]["SPAWN"] == 3
    assert json.dumps(b._data, sort_keys=True, default=str) == before


def test_empty_board():
    assert team_feed.build(Blackboard("p")) == {"types": list(team_feed.TYPES), "events": [],
                                                "spawn_tree": [], "counts": {t: 0 for t in team_feed.TYPES}}


def test_api_feed_endpoint(monkeypatch):
    """Không có httpx → gọi thẳng handler của route GET /api/feed."""
    route = next(r for r in srv.app.routes if getattr(r, "path", None) == "/api/feed")
    assert "GET" in route.methods
    monkeypatch.setattr(srv.S, "board", _board())
    out = route.endpoint(limit=100)
    assert out["types"] == list(team_feed.TYPES) and out["spawn_tree"][0]["id"] == D
    assert "RAW-THINKING" not in json.dumps(out, ensure_ascii=False)
    assert len(route.endpoint(limit=0)["events"]) == 1                            # limit kẹp ≥ 1


def test_dashboard_has_team_feed_tab():
    html = (srv.HERE / "static/index.html").read_text(encoding="utf-8")
    assert "Team feed" in html and "/api/feed" in html and "max-width:760px" in html
