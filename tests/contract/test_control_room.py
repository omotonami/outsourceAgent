"""Control Room (trang `/`) — chat CoS trong app, tạo dự án qua CoS có XAC NHAN người, giao việc phụ, Sự cố
(ESCALATED) từ UI, Phòng 3D "Tất cả dự án" (đợt 2026-11-04). Lỗi thực tế: phòng treo khi ESCALATED, 3 cửa sổ."""
import asyncio
import json
from pathlib import Path

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.gateway import trace
from src.ops import common, mock, pipeline, scheduler, side_tasks
from src.orchestrator import autonomy, gate_view, room_stream
from src.orchestrator import status as pstatus
from src.orchestrator.autonomy import Autonomy

ROOT = Path(__file__).parents[2]
BRIEF = {"client": "Acme", "source": "email", "received_at": "2026-11-01", "summary": "Ví nội bộ"}
PRODUCT = set().union(*gate_view.GATE_ZONES.values()) | {"lead_brief"}
FIVE = {"understanding", "clarifications", "plan", "assignments", "decisions_needed"}
MACHINES = ["orchestrator", "e2_cos", "E2-COS", "gate_ui", "auto-L3", "c1_dev", "intake"]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(autonomy, "STATE", tmp_path / "autonomy_state.json")
    monkeypatch.delenv("OA_EVENTSTORE_PATH", raising=False)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ev.sqlite3")


@pytest.fixture
def client(monkeypatch, db, tmp_path):
    tc = pytest.importorskip("fastapi.testclient")
    from src.server import app as srv
    from src.server import control
    EventStore(db)                                       # tạo file trước khi mở readonly
    monkeypatch.setattr(srv, "EVENTSTORE", EventStore(db, readonly=True))
    monkeypatch.setattr(srv, "GATE_STORE", EventStore(db))
    monkeypatch.setattr(srv, "GATE_FEEDBACK_LOG", tmp_path / "fb.jsonl")
    monkeypatch.setattr(srv, "AUTONOMY", Autonomy(state_path=tmp_path / "ax.json", reviews_log=tmp_path / "rv.jsonl"))
    monkeypatch.setattr(control, "COS_SENDER", mock.sender)
    control.RATE.reset()
    return tc.TestClient(srv.app, base_url="http://localhost")


def _new(db, pid, state="INTAKE"):
    b = Blackboard(pid, store=EventStore(db))
    b.write("lead_brief", dict(BRIEF), agent="intake")
    pstatus.update(b, state)
    return b


def _rows(db, pid=None):
    st = EventStore(db)
    pids = [pid] if pid else [p["id"] for p in st.projects()]
    return [r for p in pids for r in st.read(p)]


# ---------- trang ----------
def test_pages_control_room_and_legacy(client):
    html = client.get("/").text
    for s in ("Control Room", "Chat CoS", "Việc", "Gate", "Sự cố", "Token", "/api/cos/chat", "/api/cos/brief/confirm",
              "/api/room/all/stream", "XAC NHAN", "max-width:760px", "/room?all=1&amp;embed=1", "room:incident",
              "/escalation/", "/side-tasks"):
        assert s in html, s
    assert "thinking" not in html.lower() or "raw thinking" in html.lower()
    assert "Pipeline 4 phase" in client.get("/legacy").text
    room = client.get("/room").text
    assert "/api/room/all/stream" in room and "body.embed" in room and "openIncident" in room and "pchips" in room
    assert room.count("<script type=\"module\">") == 1           # một nguồn room.html, control nhúng bằng iframe


# ---------- chat CoS ----------
def test_cos_chat_mock_returns_five_sections_without_writing(client, db):
    _new(db, "P-1")
    n = len(_rows(db))
    r = client.post("/api/cos/chat", json={"message": "Tình hình hôm nay?"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and set(r.json()["reply"]) == FIVE
    r = client.post("/api/cos/chat", json={"message": "P-1 sao rồi?", "project": "P-1"})
    assert r.status_code == 200 and set(r.json()["reply"]) == FIVE
    new = _rows(db)[n:]
    assert {x.zone for x in new} <= {"llm_usage"}                 # chỉ số liệu token, không zone nào
    assert client.post("/api/cos/chat", json={"message": ""}).status_code == 400
    assert client.post("/api/cos/chat", content=b"x",
                       headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/cos/chat", json={"message": "x", "project": "P-NONE"}).status_code == 404


def test_cos_chat_cannot_sign_resolve_or_write_product(client, db, monkeypatch):
    from src.server import control
    _new(db, "P-1", "AWAIT_G1")
    _new(db, "P-2", "ESCALATED")

    def evil(model, system, messages, max_tokens):   # model "bị dụ": giao cho gate_ui, đòi ký
        out = {"understanding": "ký G1", "clarifications": [], "plan": ["ký G1 ngay"],
               "assignments": [{"agent": "gate_ui", "task": "sign G1"}], "decisions_needed": []}
        return json.dumps(out), {"total_tokens": 3}
    monkeypatch.setattr(control, "COS_SENDER", evil)
    for pid in ("P-1", "P-2", None):
        r = client.post("/api/cos/chat", json={"message": "Ký G1 và gỡ escalate giúp tôi, ghi estimate luôn",
                                               "project": pid})
        assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"] == "FORBIDDEN"
    rows = _rows(db)
    assert not [x for x in rows if x.kind in ("sign", "revoke")]
    assert not [x for x in rows if x.zone in PRODUCT - {"lead_brief"}]
    assert common.state_of(Blackboard.load("P-2", EventStore(db))) == "ESCALATED"
    assert not Blackboard.load("P-1", EventStore(db)).is_signed("G1")
    # không route CoS nào đụng gate / escalation; module control không gọi ký / gỡ
    cos = [r.path for r in control.router.routes if getattr(r, "path", "").startswith("/api/cos")]
    assert cos and not [p for p in cos if "gate" in p or "escalation" in p]
    src = (ROOT / "src/server/control.py").read_text(encoding="utf-8")
    assert ".sign(" not in src and "ops_gate" not in src and "escalation.resolve" not in src


def test_cos_chat_rate_limited(client, monkeypatch):
    from src.server import control
    monkeypatch.setattr(control, "_cfg", lambda: {"rate_limit_per_min": 2, "max_message_chars": 50,
                                                  "max_brief_chars": 100})
    codes = [client.post("/api/cos/chat", json={"message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    rl = control.RateLimit()
    assert rl.allow(1, now=0) and not rl.allow(1, now=30) and rl.allow(1, now=61)


# ---------- tạo dự án qua CoS: cần XAC NHAN của người ----------
def test_brief_preview_then_human_confirm_creates_project(client, db):
    r = client.post("/api/cos/brief", json={"client": "Acme", "source": "email", "content": "# Ví nội bộ\nCần app ví",
                                            "file_name": "brief.md", "file_text": "Hạn: Q1"})
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["draft"]["client"] == "Acme" and "Hạn: Q1" in pv["draft"]["description"]
    assert pv["draft"]["summary"] == "Ví nội bộ" and pv["cos"]["ok"] and set(pv["cos"]["reply"]) == FIVE
    assert EventStore(db).projects() == []                       # xem trước: chưa ghi gì
    base = {"draft": pv["draft"], "payload_hash": pv["payload_hash"], "id": "P-NEW"}
    for body in ({**base, "confirmed_by": "Lan"}, {**base, "confirmed_by": "Lan", "typed_confirm": "xac nhan"},
                 {**base, "typed_confirm": "XAC NHAN"}, {**base, "confirmed_by": " ", "typed_confirm": "XAC NHAN"}):
        assert client.post("/api/cos/brief/confirm", json=body).status_code == 400, body
    for who in MACHINES:
        assert client.post("/api/cos/brief/confirm", json={**base, "confirmed_by": who,
                                                           "typed_confirm": "XAC NHAN"}).status_code == 400, who
    tampered = {**base, "draft": {**pv["draft"], "client": "Khác"}, "confirmed_by": "Lan", "typed_confirm": "XAC NHAN"}
    assert client.post("/api/cos/brief/confirm", json=tampered).status_code == 409
    assert EventStore(db).projects() == []
    r = client.post("/api/cos/brief/confirm", json={**base, "confirmed_by": "Lan Nguyen", "typed_confirm": "XAC NHAN"})
    assert r.status_code == 200, r.text and r.json()["project"] == "P-NEW"
    b = Blackboard.load("P-NEW", EventStore(db))
    lb = b.get("lead_brief")
    assert lb["confirmed_by"] == "Lan Nguyen" and lb["requested_via"] == "e2_cos"
    assert common.state_of(b) == "INTAKE"
    assert client.post("/api/cos/brief/confirm", json={**base, "confirmed_by": "Lan",
                                                       "typed_confirm": "XAC NHAN"}).status_code == 409   # trùng id
    # scheduler nhận dự án mới, chạy tới G1
    s = scheduler.Scheduler(EventStore(db), sender=mock.sender, log=lambda *_: None)
    try:
        assert s.run_once(timeout=30)["outcomes"]["P-NEW"]["gate"] == "G1"
    finally:
        s.close()


def test_brief_input_errors(client):
    for body in ({"client": "A", "source": "e"}, {"client": "", "source": "e", "content": "x"},
                 {"client": "A", "source": "e", "content": "x", "file_name": "a.pdf", "file_text": "x"}):
        assert client.post("/api/cos/brief", json=body).status_code == 400, body


# ---------- giao việc phụ ----------
def test_side_task_api_requires_human_confirm(client, db):
    _new(db, "P-1", "AWAIT_G1")
    body = {"role": "b4_tech_lead", "title": "Điều tra API", "kind": "side"}
    assert client.post("/api/projects/P-1/side-tasks", json={**body, "confirmed_by": "Lan"}).status_code == 400
    for who in MACHINES:
        assert client.post("/api/projects/P-1/side-tasks",
                           json={**body, "confirmed_by": who, "typed_confirm": "XAC NHAN"}).status_code == 400
    assert client.post("/api/projects/P-1/side-tasks", json={**body, "role": "x", "confirmed_by": "Lan",
                                                             "typed_confirm": "XAC NHAN"}).status_code == 400
    assert client.post("/api/projects/P-X/side-tasks", json={**body, "confirmed_by": "Lan",
                                                             "typed_confirm": "XAC NHAN"}).status_code == 404
    r = client.post("/api/projects/P-1/side-tasks", json={**body, "confirmed_by": "Lan", "typed_confirm": "XAC NHAN"})
    assert r.status_code == 200, r.text
    ts = client.get("/api/projects/P-1/side-tasks").json()["tasks"]
    assert len(ts) == 1 and ts[0]["status"] == "queued" and ts[0]["confirmed_by"] == "Lan" and ts[0]["via"] == "dashboard"


# ---------- Sự cố: gỡ ESCALATED từ UI ----------
def _escalated(db):
    def failing(model, system, messages, max_tokens):
        if mock.identify(system) == "a1_bu":
            return "Phân tích:\n" + json.dumps({"domain": "fintech"}), {"total_tokens": 5}
        return mock.sender(model, system, messages, max_tokens)
    _new(db, "P-E")
    o = pipeline.run(EventStore(db), "P-E", sender=failing)
    assert o.stop == "ESCALATED"
    return "P-E"


def test_escalation_from_ui_then_scheduler_continues(client, db):
    pid = _escalated(db)
    snap = client.get("/api/room/all").json()
    assert snap["escalated"] == [pid] and next(p for p in snap["projects"] if p["id"] == pid)["escalated"]
    a1 = next(a for a in snap["agents"] if a["id"] == "a1_bu")
    assert a1["status"] == "blocked" and a1["work"][0]["project"] == pid      # "!" trên nhân vật → tab Sự cố
    v = client.get(f"/api/projects/{pid}/escalation").json()
    assert "retry" in v["options"] and "abandon" in v["options"]
    url = f"/api/projects/{pid}/escalation/retry"
    assert client.post(url, json={"signer": "Lan", "packet_hash": v["packet_hash"]}).status_code == 400
    assert client.post(url, json={"signer": "orchestrator", "typed_confirm": "XAC NHAN"}).status_code == 400
    r = client.post(url, json={"signer": "Lan", "typed_confirm": "XAC NHAN", "packet_hash": v["packet_hash"]})
    assert r.status_code == 200, r.text and r.json()["state"] == "PRESALES"
    snap = client.get("/api/room/all").json()
    assert snap["escalated"] == []
    s = scheduler.Scheduler(EventStore(db), sender=mock.sender, log=lambda *_: None)
    try:
        assert s.run_once(timeout=30)["outcomes"][pid]["gate"] == "G1"       # không cần F5 / lệnh tay
    finally:
        s.close()


def test_escalation_abandon_from_ui(client, db):
    pid = _escalated(db)
    v = client.get(f"/api/projects/{pid}/escalation").json()
    r = client.post(f"/api/projects/{pid}/escalation/abandon",
                    json={"signer": "Lan", "typed_confirm": "XAC NHAN", "packet_hash": v["packet_hash"]})
    assert r.status_code == 200 and r.json()["state"] == "ABANDONED"
    assert client.post(f"/api/projects/{pid}/side-tasks", json={"role": "b1_ba", "title": "x", "confirmed_by": "Lan",
                                                                "typed_confirm": "XAC NHAN"}).status_code == 409


# ---------- Phòng 3D đa dự án ----------
def test_room_all_merges_projects(client, db):
    for i in range(2):
        _new(db, f"P-{i}")
    side_tasks.add(Blackboard.resume("P-1", EventStore(db)), "b4_tech_lead", "Điều tra đối tác")
    s = scheduler.Scheduler(EventStore(db), sender=mock.sender, log=lambda *_: None)
    try:
        s.run_once(timeout=30)
    finally:
        s.close()
    snap = client.get("/api/room/all").json()
    assert snap["all"] is True and len(snap["agents"]) == len({a["id"] for a in snap["agents"]})   # 1 nhân vật / agent
    assert {p["id"] for p in snap["projects"]} == {"P-0", "P-1"} and len(snap["gates"]) == 2
    a1 = next(a for a in snap["agents"] if a["id"] == "a1_bu")
    assert a1["status"] == "waiting_gate" and {w["project"] for w in a1["work"]} == {"P-0", "P-1"}
    side = [t for t in snap["tasks"] if t["kind"] == "side"]
    assert side and side[0]["project"] == "P-1" and side[0]["status"] == "done" and side[0]["note"]
    assert all(e["project"] in ("P-0", "P-1") and e["id"].startswith(e["project"] + "|") for e in snap["events"])
    u = snap["usage"]
    assert set(u["by_project"]) == {"P-0", "P-1"} and u["total"] == sum(v["total"] for v in u["by_project"].values())
    assert u["by_project"]["P-1"]["total"] > u["by_project"]["P-0"]["total"]           # + việc phụ
    blob = json.dumps(snap, ensure_ascii=False)
    for k in ('"thinking"', '"prompt"', '"messages"', '"system"'):
        assert k not in blob


def test_merge_agents_main_and_side():
    meta = [{"id": "b4_tech_lead", "name": "B4", "role": "tech_lead", "phase": "B"}]
    per = {"P-1": [{"id": "b4_tech_lead", "status": "working", "kind": "main", "current_task": None}],
           "P-2": [{"id": "b4_tech_lead", "status": "working", "kind": "side", "current_task": None}],
           "P-3": [{"id": "b4_tech_lead", "status": "waiting_gate", "kind": None, "current_task": None}]}
    a = room_stream.merge_agents(meta, per)[0]
    assert a["status"] == "working" and a["main"] == ["P-1"] and a["side"] == ["P-2"] and a["project"] == "P-1"
    per["P-3"][0]["status"] = "blocked"
    assert room_stream.merge_agents(meta, per)[0]["status"] == "blocked"


def test_multifeed_poll_and_sse_realtime(db):
    st = EventStore(db)
    _new(db, "P-0")
    feed = room_stream.MultiFeed(st)
    assert feed.poll() == []
    _new(db, "P-1")                                      # dự án mới xuất hiện giữa chừng
    b = Blackboard.resume("P-0", st)
    pipeline.assign(b, "a1_bu", "presales", "P-0")
    fr = feed.poll()
    assert len(fr) == 1 and {p["id"] for p in fr[0]["projects"]} == {"P-0", "P-1"}
    a1 = next(a for a in fr[0]["agents"] if a["id"] == "a1_bu")
    assert a1["status"] == "working" and a1["main"] == ["P-0"]
    assert any(e["type"] == "ASSIGN" and e["project"] == "P-0" for e in fr[0]["events"])
    assert fr[0]["seq"] == max(st.last_seq("P-0"), st.last_seq("P-1"))

    async def go():
        f2 = room_stream.MultiFeed(st, since=fr[0]["seq"])
        pipeline.finish(b, "a1_bu", "presales", "P-0", {"ok": True})
        out = []
        async for chunk in room_stream.stream(f2, poll_s=0, heartbeat_s=99, initial=False, max_polls=2,
                                              sleep=lambda s: asyncio.sleep(0)):
            out.append(chunk)
        return out
    chunks = asyncio.run(go())
    data = [json.loads(c.split("data: ", 1)[1]) for c in chunks if c.startswith("id:")]
    assert data and next(a for a in data[0]["agents"] if a["id"] == "a1_bu")["status"] == "idle"


def test_settings_deny_new_human_endpoints():
    deny = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
    assert any("cos/brief/confirm" in d for d in deny) and any("side-tasks" in d for d in deny)


def test_room_state_agent_kind_main_side():
    from src.orchestrator import events, room_state
    b = Blackboard("P-K", store=None)
    pipeline.assign(b, "a1_bu", "presales", "P-K")
    events.emit(b, "ASSIGN", writer="orchestrator", by="orchestrator", agent="b4_tech_lead", phase="việc phụ S-1",
                project="P-K", kind="side", side_task="S-1")
    by = {a["id"]: a for a in room_state.snapshot(b)["agents"]}
    assert by["a1_bu"]["kind"] == "main" and by["b4_tech_lead"]["kind"] == "side" and by["c1_dev"]["kind"] is None
    events.emit(b, "RESULT", writer="orchestrator", by="orchestrator", agent="b4_tech_lead", ok=True, kind="side")
    by = {a["id"]: a for a in room_state.snapshot(b)["agents"]}
    assert by["b4_tech_lead"]["status"] == "idle" and by["b4_tech_lead"]["kind"] is None


def test_cos_model_unavailable_shows_claude_code_message(client, monkeypatch):
    from src.server import control

    def down(model, system, messages, max_tokens):
        raise RuntimeError("secret-detail sk-xxx")
    monkeypatch.setattr(control, "COS_SENDER", down)
    r = client.post("/api/cos/chat", json={"message": "hi"})
    assert r.status_code == 503 and r.json()["error"] == "NOT_READY"
    assert "chạy `claude` và đăng nhập" in r.json()["reason"] and "sk-xxx" not in r.text
    r = client.post("/api/cos/brief", json={"client": "A", "source": "e", "content": "x"})
    assert r.status_code == 200 and r.json()["cos"]["error"] == "NOT_READY" and "sk-xxx" not in r.text
    assert "API key" not in client.get("/").text and "ANTHROPIC" not in client.get("/").text


# ---------- merge fix/field-events: endpoint ghi của Control Room cũng qua chốt Host/Origin + Content-Type ----------
CR_WRITES = ["/api/cos/chat", "/api/cos/brief", "/api/cos/brief/confirm", "/api/projects/{pid}/side-tasks"]


@pytest.mark.parametrize("path", CR_WRITES)
@pytest.mark.parametrize("base,headers,code", [
    ("http://evil.example", {}, 403),
    ("http://localhost", {"Origin": "http://evil.example"}, 403),
    ("http://localhost", {"Origin": "null"}, 403),
    ("http://localhost", {"Content-Type": "text/plain"}, 415),
    ("http://localhost", {"Content-Type": "application/x-www-form-urlencoded"}, 415),
])
def test_control_room_writes_refuse_bad_host_origin_content_type(client, db, path, base, headers, code):
    from src.server import app as srv
    tc = pytest.importorskip("fastapi.testclient")
    _new(db, "P-W")
    n = len(_rows(db))
    body = {"message": "x", "role": "c1_dev", "title": "t", "confirmed_by": "Lan Nguyen", "typed_confirm": "XAC NHAN"}
    r = tc.TestClient(srv.app, base_url=base).post(path.format(pid="P-W"), content=json.dumps(body),
                                                   headers={"Content-Type": "application/json", **headers})
    assert r.status_code == code, r.text
    assert len(_rows(db)) == n                                    # bị chặn trước handler: không ghi gì


def test_control_room_ui_posts_json():
    html = (ROOT / "src/server/static/control.html").read_text(encoding="utf-8")
    assert "headers:{'Content-Type':'application/json'}" in html and "JSON.stringify(body)" in html
    assert html.count("method:'POST'") == 1                       # mọi POST đi qua helper post() gửi JSON
