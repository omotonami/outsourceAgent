"""Event store dùng chung (SQLite) + Phòng 3D realtime: ghi xuyên, dựng lại, SSE, /api/projects, token đã đốt.
Offline, không API key, không httpx (gọi thẳng handler / generator)."""
import asyncio
import json
import multiprocessing as mp
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from jsonschema import ValidationError

from src.blackboard import eventstore
from src.blackboard.eventstore import EventStore
from src.blackboard.proxy import ReadOnlyBoard, WorkerBoard
from src.blackboard.store import Blackboard, PermissionDenied
from src.gateway import llm_client, trace
from src.orchestrator import events, room_state, room_stream, usage
from src.orchestrator.dispatcher import Dispatcher
from src.server import app as srv

ROOT = Path(__file__).parents[2]
D = "orchestrator.dispatcher"


def _pid(tag="es"):
    return f"{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "ev.sqlite3")


@pytest.fixture(autouse=True)
def _trace_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")


def _raw_rows(path):
    with sqlite3.connect(path) as c:
        return c.execute("SELECT seq, project_id, kind, zone, agent, payload_json FROM events ORDER BY seq").fetchall()


# ---------------- store ----------------
def test_append_read_roundtrip_and_readonly(store, tmp_path):
    s1 = store.append("p1", "zone_write", {"a": 1}, zone="estimate", agent="a2_estimation")
    s2 = store.append("p2", "event", {"event": "SPAWN"}, zone="trace_log", agent="orchestrator")
    s3 = store.append("p1", "sign", {"gate": "G1", "by": "human", "via": "gate_ui"})
    assert s1 < s2 < s3
    rows = store.read("p1")
    assert [(r.seq, r.kind, r.zone, r.payload) for r in rows] == [(s1, "zone_write", "estimate", {"a": 1}),
                                                                  (s3, "sign", None, {"gate": "G1", "by": "human", "via": "gate_ui"})]
    assert [r.seq for r in store.read("p1", after=s1)] == [s3] and store.last_seq("p2") == s2
    with pytest.raises(ValueError):
        store.append("p1", "update", {})
    ro = EventStore(store.path, readonly=True)
    assert [r.seq for r in ro.read("p1")] == [s1, s3]
    with pytest.raises(PermissionError):
        ro.append("p1", "event", {})
    with pytest.raises(PermissionError):
        ro.prune(time.time())
    missing = EventStore(tmp_path / "nope.sqlite3", readonly=True)
    assert missing.read("p1") == [] and missing.projects() == [] and not (tmp_path / "nope.sqlite3").exists()
    with sqlite3.connect(store.path) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_prune_is_explicit_retention(store):
    old = store.append("p", "event", {"x": 1}, zone="trace_log", ts=1000.0)
    new = store.append("p", "event", {"x": 2}, zone="trace_log")
    assert eventstore.retention_cutoff(now=100 * 86400) == 70 * 86400      # specs: drafts_days 30
    assert store.prune(2000.0) == 1 and [r.seq for r in store.read("p")] == [new] and old < new


def _writer(path, pid, n):
    s = EventStore(path)
    for i in range(n):
        s.append(pid, "event", {"i": i, "who": pid}, zone="trace_log", agent="orchestrator")


def test_many_processes_write_concurrently_no_loss_no_dup(store):
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(store.path), f"proc{k}", 40)) for k in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    rows = _raw_rows(store.path)
    seqs = [r[0] for r in rows]
    assert len(rows) == 160 and len(set(seqs)) == 160 and seqs == sorted(seqs)
    for k in range(4):
        mine = [json.loads(r[5])["i"] for r in rows if r[1] == f"proc{k}"]
        assert mine == list(range(40))                       # thứ tự trong mỗi process giữ nguyên


# ---------------- Blackboard ghi xuyên ----------------
def _worker(contract, h):
    h.report("testing")
    h.report("qc")
    return {"ok": True}


def test_default_board_has_no_store():
    assert eventstore.settings()["write_through"] is False
    assert Blackboard(_pid())._store is None


def test_rebuilt_board_matches_original(store):
    pid = _pid()
    b = Blackboard(pid, store=store)
    b.write("lead_brief", {"client": "NeoPay", "source": "email", "received_at": "2026-10-25"}, agent="intake")
    b.write("task_backlog", [{"id": "T1", "spec": "a", "AC": ["x"], "owns": ["a/"]},
                             {"id": "T2", "spec": "b", "AC": ["y"], "deps": ["T1"], "owns": ["b/"]}],
            agent="b4_tech_lead")
    b.append("risk_register", {"risk": "FX", "severity": "High", "status": "open"}, agent="a1_bu")
    events.record(b, "huddle_log", {"round": 1, "agent": "b1_ba", "role": "proposer", "summary": "s"},
                  agent="huddle", allowed=("huddle_log",))
    Dispatcher(b, _worker).run()
    b.sign("G1", by="human", via="gate_ui")
    b.sign("G2", by="human", via="gate_ui")
    b.revoke_signature("G2", "đổi scope", by="pm")
    r = Blackboard.load(pid, store)
    assert r._data == b._data and r._signatures == b._signatures and r._store is None
    assert [z for z, *_ in r._history] == [z for z, *_ in b._history]
    kinds = {row.kind for row in store.read(pid)}
    assert kinds == {"zone_write", "zone_append", "event", "sign", "revoke"}
    assert all(row.kind == "event" for row in store.read(pid) if row.zone == "trace_log")
    snap_a = room_state.snapshot(b, limit=None)
    snap_b = room_state.snapshot(ReadOnlyBoard(r), limit=None)
    assert snap_a == snap_b
    # board dựng lại không ghi xuyên; board có store không được replay
    with pytest.raises(PermissionDenied):
        b._replay(store.read(pid)[0])
    n = store.last_seq(pid)
    r.write("proposal", {"x": 1}, agent="a3_proposal")
    assert store.last_seq(pid) == n
    assert Blackboard.load(pid, store, upto=store.read(pid)[0].seq)._data.keys() == {"lead_brief"}


def test_denied_writes_never_reach_store(store):
    pid = _pid()
    b = Blackboard(pid, store=store)
    with pytest.raises(PermissionDenied):
        b.write("estimate", {"wbs": []}, agent="c1_dev")                 # sai writer
    with pytest.raises(PermissionDenied):
        b.append("estimate", {"x": 1}, agent="a2_estimation")            # sai mode
    with pytest.raises(ValidationError):
        b.write("lead_brief", {"bad": 1}, agent="intake")                # sai schema
    for who in ("orchestrator", "auto-approver", ""):
        with pytest.raises(PermissionDenied):
            b.sign("G1", by=who, via="gate_ui")
    with pytest.raises(PermissionDenied):
        ReadOnlyBoard(b).write("proposal", {}, agent="a3_proposal")
    with pytest.raises(PermissionDenied):
        WorkerBoard(b, ["c1_dev"]).write("proposal", {}, agent="a3_proposal")
    with pytest.raises(PermissionDenied):
        WorkerBoard(b, ["c1_dev"], cancelled=lambda: True).append("risk_register", {"risk": "x"}, agent="c1_dev")
    b.revoke_signature("G9", "không có", by="pm")                         # không có chữ ký → không ghi
    assert store.read(pid) == []
    # endpoint room/projects chỉ đọc; ngoại lệ DUY NHẤT: người ký gate dự án (src/ops/gate.py, 2026-11-04)
    writers = [r.path for r in srv.app.routes if getattr(r, "path", "").startswith(("/api/room", "/api/projects"))
               and not r.methods <= {"GET", "HEAD"}]
    assert writers == ["/api/projects/{pid}/gate/{gid}/{decision}"]


def test_store_failure_leaves_ram_unchanged(store):
    class Broken:
        def append(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O")
    b = Blackboard(_pid(), store=Broken())
    with pytest.raises(sqlite3.OperationalError):
        b.write("proposal", {"x": 1}, agent="a3_proposal")
    assert b.get("proposal") is None


def test_payload_scrubbed_no_raw_thinking_or_prompt(store):
    pid = _pid()
    b = Blackboard(pid, store=store)
    b.append("trace_log", {"event": "SPAWN", "task_id": "T1", "ts": 1.0, "by": D, "thinking": "RAW-THINKING",
                           "contract": {"goal": "RAW-CONTRACT"}, "nested": {"messages": ["RAW-PROMPT"], "ok": 1}},
             agent="orchestrator")
    body = " ".join(r[5] for r in _raw_rows(store.path))
    assert "RAW-" not in body and '"ok": 1' in body
    feed = room_stream.RoomFeed(EventStore(store.path, readonly=True), pid)
    assert "RAW-" not in json.dumps(feed.snapshot(), ensure_ascii=False)


# ---------------- RoomFeed / SSE ----------------
def _project(store, pid):
    b = Blackboard(pid, store=store)
    b.write("task_backlog", [{"id": "T1", "spec": "KYC", "AC": ["x"], "owns": ["a/"]},
                             {"id": "T2", "spec": "FX", "AC": ["y"], "owns": ["b/"]}], agent="b4_tech_lead")
    Dispatcher(b, _worker).run()
    return b


def _parse(chunks):
    out = []
    for c in chunks:
        if not c.startswith(("id:", "event:")):
            continue
        d = dict(line.split(": ", 1) for line in c.strip().split("\n"))
        out.append({"id": int(d["id"]) if "id" in d else None, "event": d["event"], "data": json.loads(d["data"])})
    return out


async def _collect(gen, n=None):
    out = []
    async for c in gen:
        out.append(c)
        if n and len(out) >= n:
            break
    await gen.aclose()
    return out


async def _nosleep(_):
    return None


def test_feed_frames_after_since_and_snapshot(store):
    pid = _pid()
    _project(store, pid)
    rows = store.read(pid)
    mid = rows[len(rows) // 2].seq
    full = room_stream.RoomFeed(store, pid)
    assert full.last == rows[-1].seq and full.snapshot()["seq"] == rows[-1].seq
    frames = room_stream.RoomFeed(store, pid, since=mid).poll()
    assert frames and all(f["seq"] > mid for f in frames)
    assert [f["seq"] for f in frames] == sorted(f["seq"] for f in frames)
    ids_after = [e["id"] for f in frames for e in f["events"]]
    ids_before = {e["id"] for e in room_state.events(ReadOnlyBoard(Blackboard.load(pid, store, upto=mid)))}
    assert ids_after and not set(ids_after) & ids_before and len(ids_after) == len(set(ids_after))
    all_ids = {e["id"] for e in room_state.events(ReadOnlyBoard(Blackboard.load(pid, store)))}
    assert set(ids_after) | ids_before == all_ids                         # không mất event nào
    assert frames[-1]["tasks"] == full.snapshot()["tasks"]
    assert all(set(f) == {"seq", "ts", "project", "events", "agents", "tasks", "gate", "usage"} for f in frames)


def test_sse_generator_since_heartbeat_and_initial_snapshot(store):
    pid = _pid()
    _project(store, pid)
    rows = store.read(pid)
    since = rows[3].seq
    feed = room_stream.RoomFeed(store, pid, since=since)
    t = iter(range(0, 1000, 20))
    chunks = asyncio.run(_collect(room_stream.stream(feed, poll_s=0, heartbeat_s=15, sleep=_nosleep,
                                                     clock=lambda: next(t), max_polls=3)))
    assert chunks[0] == "retry: 2000\n\n" and ": hb\n\n" in chunks
    msgs = _parse(chunks)
    assert msgs and all(m["event"] == "room" and m["id"] > since and m["id"] == m["data"]["seq"] for m in msgs)
    assert [m["id"] for m in msgs] == [f["seq"] for f in room_stream.RoomFeed(store, pid, since=since).poll()]
    # không since → snapshot trước (id = seq cuối), rồi chỉ frame mới
    feed2 = room_stream.RoomFeed(store, pid)
    first = _parse(asyncio.run(_collect(room_stream.stream(feed2, poll_s=0, heartbeat_s=15, initial=True,
                                                           sleep=_nosleep, max_polls=1))))
    assert [m["event"] for m in first] == ["snapshot"] and first[0]["id"] == rows[-1].seq
    # dòng mới sau đó → frame mới
    Blackboard(pid, store=store).append("trace_log", {"event": "BLOCKED", "task_id": "T1", "ts": time.time(),
                                                      "by": D, "reason": "sandbox"}, agent="orchestrator")
    nxt = feed2.poll()
    assert len(nxt) == 1 and nxt[0]["events"][0]["type"] == "BLOCKED"


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}

    async def is_disconnected(self):
        return False


def test_sse_endpoint_resumes_with_last_event_id(store, monkeypatch):
    pid = _pid()
    _project(store, pid)
    monkeypatch.setattr(srv, "EVENTSTORE", EventStore(store.path, readonly=True))
    rows = store.read(pid)
    resume = rows[-3].seq
    resp = asyncio.run(srv.room_stream_sse(_Req({"last-event-id": str(resume)}), pid, since=0))
    assert resp.media_type == "text/event-stream"
    want = [f["seq"] for f in room_stream.RoomFeed(store, pid, since=resume).poll()]
    msgs = _parse(asyncio.run(_collect(resp.body_iterator, n=1 + len(want))))
    assert [m["id"] for m in msgs] == want and all(i > resume for i in want)
    bad = asyncio.run(srv.room_stream_sse(_Req(), "không-có", since=None))
    assert bad.status_code == 404


def test_api_projects_room_and_frames(store, monkeypatch):
    live, done = _pid("live"), _pid("done")
    _project(store, done)
    old = time.time() - 3600
    with sqlite3.connect(store.path) as c:
        c.execute("UPDATE events SET ts=? WHERE project_id=?", (old, done))     # test giả lập dự án cũ
    b = _project(store, live)
    from src.orchestrator import status as pstatus
    pstatus.update(b, "build")
    monkeypatch.setattr(srv, "EVENTSTORE", EventStore(store.path, readonly=True))
    out = srv.projects()
    ps = out["projects"]
    assert [p["id"] for p in ps] == [live, done]
    assert ps[0]["active"] and not ps[1]["active"] and ps[0]["phase"] == "C" and ps[0]["state"] == "build"
    assert ps[0]["last_seq"] == store.last_seq(live) and out["pricing"]["opus"]["out"] > 0
    snap = srv.room(project=live)
    assert snap["project"] == live and snap["seq"] == store.last_seq(live) and "usage" in snap
    assert {t["id"] for t in snap["tasks"]} == {"T1", "T2"} and snap["events"]
    assert srv.room(project="nope").status_code == 404
    dash = srv.room()
    assert "project" not in dash and "usage" in dash                         # DASH như cũ (+ usage)
    fr = srv.room_frames(project=done)
    seqs = [f["seq"] for f in fr["frames"]]
    assert seqs and seqs == sorted(seqs) and seqs[-1] <= store.last_seq(done)


def test_server_never_writes_store(store, monkeypatch, tmp_path):
    pid = _pid()
    _project(store, pid)
    ro = EventStore(store.path, readonly=True)
    monkeypatch.setattr(srv, "EVENTSTORE", ro)
    before = _raw_rows(store.path)
    srv.projects(); srv.room(project=pid); srv.room_frames(project=pid)
    asyncio.run(_collect(asyncio.run(srv.room_stream_sse(_Req(), pid, since=None)).body_iterator, n=2))
    assert _raw_rows(store.path) == before
    assert srv.S.board._store is None


# ---------------- token đã đốt ----------------
def _call(pid, agent, tier, tin, tout, secret="SECRET-PROMPT"):
    llm_client.call(agent, tier, f"system {secret}", [{"role": "user", "content": secret}], pid, "build",
                    max_tokens=tout, sender=lambda m, s, msg, mt: (f"output {secret}",
                                                                   {"input_tokens": tin, "output_tokens": tout,
                                                                    "total_tokens": tin + tout}))


def test_usage_aggregated_from_gateway_trace(store):
    pid = _pid("use")
    Blackboard(pid, store=store)                     # project ghi xuyên → trace gateway vào cùng store
    _call(pid, "c1_dev", "opus", 1000, 500)
    _call(pid, "c1_dev", "opus", 2000, 1000)
    _call(pid, "c2_tester", "sonnet", 300, 100)
    _call(pid, "huddle:b1_ba", "haiku", 50, 50)
    rows = [r for r in store.read(pid) if r.zone == usage.ZONE]
    assert len(rows) == 4 and all(r.kind == "event" for r in rows)
    assert set(rows[0].payload) == {"agent", "tier", "model", "phase", "tokens_in", "tokens_out", "total"}
    body = " ".join(r[5] for r in _raw_rows(store.path))
    assert "SECRET" not in body                                               # không lộ prompt/output
    u = room_stream.RoomFeed(store, pid).snapshot()["usage"]
    assert (u["tokens_in"], u["tokens_out"], u["total"], u["calls"]) == (3350, 1650, 5000, 4)
    assert u["by_agent"] == {"c1_dev": 4500, "c2_tester": 400, "huddle:b1_ba": 100}
    assert u["by_tier"]["opus"] == {"tokens_in": 3000, "tokens_out": 1500, "total": 4500, "calls": 2}
    p = usage.prices()
    want = 3000 * p["opus"][0] + 1500 * p["opus"][1] + 300 * p["sonnet"][0] + 100 * p["sonnet"][1] \
        + 50 * p["haiku"][0] + 50 * p["haiku"][1]
    assert u["est_cost_usd"] == pytest.approx(want, abs=1e-6)
    assert Blackboard.load(pid, store)._data == {}                           # usage không thành zone Blackboard


def test_usage_frame_pushed_on_new_call(store):
    pid = _pid("use")
    Blackboard(pid, store=store)
    _call(pid, "c1_dev", "sonnet", 10, 10)
    feed = room_stream.RoomFeed(store, pid)
    _call(pid, "c3_qc", "opus", 100, 50)
    fr = feed.poll()
    assert len(fr) == 1 and fr[0]["usage"]["total"] == 170 and fr[0]["usage"]["by_agent"]["c3_qc"] == 150


def test_pricing_and_budget_read_from_config(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/pricing.yaml").write_text(
        "per_million_tokens:\n  sonnet: {in: 2.0, out: 10.0}\n", encoding="utf-8")
    (tmp_path / "config/budgets.yaml").write_text(
        "warn_at_ratio: 0.8\nper_project_tokens: {presales: 1000, build: 3000, discovery: null}\n", encoding="utf-8")
    p = usage.prices(tmp_path)
    assert p == {"sonnet": (2e-6, 1e-5)}
    bud = usage.budget(tmp_path)
    assert bud == {"limit": 4000, "warn_at_ratio": 0.8}
    u = usage.Usage()
    u.add("c1_dev", "sonnet", 1000, 2400)
    d = u.to_dict(p, bud)
    assert d["est_cost_usd"] == pytest.approx(1000 * 2e-6 + 2400 * 1e-5)
    assert d["budget"]["ratio"] == pytest.approx(0.85) and d["budget"]["used"] == 3400
    assert usage.budget() is None                                  # repo: per_project_tokens toàn null
    from src.server import demo
    assert not hasattr(demo, "PRICE")                              # bảng giá hard-code đã gỡ
    st = next(s for s in demo.plan(True) if s.get("tier") == "opus")
    assert demo.step_cost(st) == pytest.approx(usage.step_cost("opus", st["tok"]))


# ---------------- end-to-end: process khác ghi, process này đọc ----------------
def test_live_team_in_other_process_visible_here(tmp_path):
    db = tmp_path / "live.sqlite3"
    pid = _pid("LIVE")
    proc = subprocess.Popen([sys.executable, "-m", "src.demo.live_team", "--db", str(db), "--pace", "0.02",
                             "--project", pid], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ro = EventStore(db, readonly=True)
    seen_running = False
    deadline = time.time() + 60
    while proc.poll() is None and time.time() < deadline:
        if ro.last_seq(pid) > 0:
            snap = room_stream.RoomFeed(ro, pid).snapshot()
            seen_running = seen_running or bool(snap["agents"])
        time.sleep(0.05)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 0, out
    assert seen_running                                            # đọc được trong lúc process kia đang ghi
    snap = room_stream.RoomFeed(ro, pid).snapshot(limit=None)
    assert {t["id"]: t["status"] for t in snap["tasks"]} == {f"T{i}": "await_merge" for i in range(1, 6)}
    types = {e["type"] for e in snap["events"]}
    assert {"ASSIGN", "SPAWN", "RESULT", "DECISION"} <= types
    assert any(e["type"] == "RESULT" and "FAIL" in e["summary"] for e in snap["events"])   # T2 trượt QC rồi retry
    assert snap["usage"]["total"] > 0 and {"c1_dev", "c2_tester", "c3_qc"} <= set(snap["usage"]["by_agent"])
    assert {a["id"] for a in snap["agents"]} >= {"c1_dev", "b4_tech_lead"}
    ps = room_stream.projects(ro)
    assert ps[0]["id"] == pid and ps[0]["active"]
