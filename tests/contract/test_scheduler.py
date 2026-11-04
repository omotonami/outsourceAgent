"""src/ops/scheduler.py + src/ops/side_tasks.py — đa dự án "việc chính + việc phụ" (đợt Control Room 2026-11-04).
Lỗi thực tế: không chạy nhiều dự án / task giao thêm; team thật làm việc chính và còn điều tra task dự án khác."""
import json
import threading
import time
from collections import defaultdict
from pathlib import Path

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.proxy import ReadOnlyBoard
from src.blackboard.store import Blackboard, PermissionDenied
from src.gateway import trace
from src.ops import common, mock, project, scheduler, side_tasks
from src.orchestrator import autonomy, gate_view
from src.orchestrator import status as pstatus

ROOT = Path(__file__).parents[2]
PRODUCT = set().union(*gate_view.GATE_ZONES.values()) | {"lead_brief", "gate_signatures", "project_status"}
BRIEF = {"client": "Acme", "source": "email", "received_at": "2026-11-01", "summary": "Ví nội bộ"}


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(autonomy, "STATE", tmp_path / "autonomy_state.json")
    monkeypatch.delenv("OA_EVENTSTORE_PATH", raising=False)


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "ev.sqlite3")


def _new(store, pid):
    b = Blackboard(pid, store=store)
    b.write("lead_brief", dict(BRIEF), agent="intake")
    pstatus.update(b, "INTAKE")
    return b


def _cfg(**kw):
    return {**scheduler.settings(), **kw}


class Probe:
    """Sender mock chậm, đếm số lượt đồng thời theo role (main vs side) và tổng."""

    def __init__(self, delay=0.03, fail=None):
        self.delay, self.fail = delay, fail
        self.lock = threading.Lock()
        self.now = defaultdict(int)
        self.peak = defaultdict(int)
        self.total = self.peak_total = 0
        self.order = []

    def __call__(self, model, system, messages, max_tokens):
        who = mock.identify(system)
        if who == mock.SIDE:
            who = "side:" + json.loads(messages[0]["content"])["task"]["id"]
        with self.lock:
            self.now[who] += 1
            self.peak[who] = max(self.peak[who], self.now[who])
            self.total += 1
            self.peak_total = max(self.peak_total, self.total)
            self.order.append(who)
        try:
            time.sleep(self.delay)
            if self.fail and who == self.fail:
                raise RuntimeError("mạng lỗi (giả)")
            return mock.sender(model, system, messages, max_tokens)
        finally:
            with self.lock:
                self.now[who] -= 1
                self.total -= 1


def _state(store, pid):
    return common.state_of(Blackboard.load(pid, store))


def _trace(store, pid, event=None):
    return [e for e in Blackboard.load(pid, store).get("trace_log") or [] if event in (None, e.get("event"))]


def test_three_projects_parallel_to_gate_with_role_caps(store):
    for i in range(3):
        _new(store, f"P-{i}")
    probe = Probe()
    s = scheduler.Scheduler(store, sender=probe, cfg=_cfg(max_concurrent_calls=4), log=lambda *_: None)
    try:
        out = s.run_once(timeout=60)
    finally:
        s.close()
    assert {p: o["stop"] for p, o in out["outcomes"].items()} == {f"P-{i}": "AWAIT_GATE" for i in range(3)}
    assert all(_state(store, f"P-{i}") == "AWAIT_G1" for i in range(3))
    # trần việc chính / role = 1: không role nào chạy 2 dự án cùng lúc …
    assert max(v for (r, k), v in s.slots.peak.items() if k == "main") == 1
    assert all(probe.peak[a] == 1 for a in ("a1_bu", "a2_estimation", "a3_proposal"))
    # … nhưng 3 dự án chạy SONG SONG (role khác nhau cùng lúc), không quá trần lượt chạy
    assert 2 <= probe.peak_total <= 4 and s.slots.peak_calls <= 4
    for i in range(3):                                   # ASSIGN mang project + kind
        a = [e for e in _trace(store, f"P-{i}", "ASSIGN")]
        assert a and all(e["project"] == f"P-{i}" and e["kind"] == "main" for e in a)


def test_max_concurrent_calls_respected(store):
    for i in range(3):
        _new(store, f"P-{i}")
    probe = Probe()
    s = scheduler.Scheduler(store, sender=probe, cfg=_cfg(max_concurrent_calls=1), log=lambda *_: None)
    try:
        s.run_once(timeout=60)
    finally:
        s.close()
    assert probe.peak_total == 1 and s.slots.peak_calls == 1
    assert all(_state(store, f"P-{i}") == "AWAIT_G1" for i in range(3))


def test_side_task_waits_until_role_free_and_writes_only_note(store):
    for i in range(2):
        _new(store, f"P-{i}")
    t = side_tasks.add(Blackboard.resume("P-1", store), "a1_bu", "Điều tra scope đối tác", detail="đọc brief")
    before = {r.seq for r in store.read("P-1")}
    probe = Probe()
    s = scheduler.Scheduler(store, sender=probe, cfg=_cfg(), log=lambda *_: None)
    try:
        s.run_once(timeout=60)
    finally:
        s.close()
    side = "side:" + t["id"]
    assert side in probe.order
    # ưu tiên việc chính: việc phụ của A1 chỉ chạy sau mọi lượt A1 chính (cả 2 dự án ở presales)
    assert probe.order.index(side) > max(i for i, w in enumerate(probe.order) if w == "a1_bu")
    task = side_tasks.tasks(Blackboard.load("P-1", store))[0]
    assert task["status"] == "done" and task["note"]["summary"].startswith("Điều tra (mock)")
    # việc phụ KHÔNG ghi zone sản phẩm: dòng mới của task chỉ ở side_tasks / trace_log / llm_usage
    rows = [r for r in store.read("P-1") if r.seq not in before]
    side_rows = [r for r in rows if r.zone == "side_tasks"
                 or (r.zone == "trace_log" and r.payload.get("side_task") == t["id"])]
    assert {r.zone for r in side_rows} == {"side_tasks", "trace_log"}
    kinds = [(r.payload["event"], r.payload["kind"]) for r in side_rows if r.zone == "trace_log"]
    assert kinds == [("ASSIGN", "side"), ("RESULT", "side")]
    assert s.slots.peak[("a1_bu", "side")] == 1


def test_side_cap_per_role(store):
    for i in range(3):                                   # 3 dự án ở gate: role rảnh, 3 việc phụ cùng role
        b = _new(store, f"P-{i}")
        pstatus.update(b, "AWAIT_G1")
        side_tasks.add(Blackboard.resume(f"P-{i}", store), "b4_tech_lead", f"điều tra {i}")
    probe = Probe(delay=0.05)
    s = scheduler.Scheduler(store, sender=probe, cfg=_cfg(max_side_per_role=1), log=lambda *_: None)
    try:
        s.run_once(timeout=60)
    finally:
        s.close()
    assert s.slots.peak[("b4_tech_lead", "side")] == 1 and probe.peak_total == 1
    assert len(s.tasks_done) == 3 and all(t["event"] == "done" for t in s.tasks_done)
    s2 = scheduler.Slots(1, 2, 4)                         # trần 2: 2 việc phụ cùng role được, cái thứ 3 không
    assert s2.acquire(("x",), "P1", "side", block=False) and s2.acquire(("x",), "P2", "side", block=False)
    assert not s2.acquire(("x",), "P3", "side", block=False)


def test_slots_main_blocks_side_and_waiting_main_has_priority():
    s = scheduler.Slots(1, 1, 4)
    assert s.acquire(("a1_bu",), "P1", "main")
    assert not s.acquire(("a1_bu",), "P2", "side", block=False)        # role đang việc chính
    got = []
    th = threading.Thread(target=lambda: got.append(s.acquire(("a1_bu",), "P2", "main")))
    th.start()
    time.sleep(0.1)
    assert not got and s.waiting["a1_bu"] == 1                          # việc chính thứ 2 chờ (trần 1)
    s.release(("a1_bu",), "P1", "main")
    th.join(3)
    assert got == [True]
    assert not s.acquire(("a1_bu",), "P3", "side", block=False)
    s.release(("a1_bu",), "P2", "main")
    assert s.acquire(("a1_bu",), "P3", "side", block=False)


def test_side_task_board_is_read_only(store):
    b = _new(store, "P-0")
    from src.agents import side_task
    ctx = side_task.context(b, "a1_bu")
    assert ctx["project_status"]["state"] == "INTAKE"
    rb = ReadOnlyBoard(b, owner="side:a1_bu")
    for f in (lambda: rb.write("business_context", {}, agent="a1_bu"), lambda: rb.sign("G1", by="x")):
        with pytest.raises(PermissionDenied):
            f()
    with pytest.raises(PermissionDenied):                # orchestrator không ghi được zone sản phẩm qua side zone
        b.append("business_context", {"x": 1}, agent="orchestrator")
    with pytest.raises(PermissionDenied):                # side_tasks chỉ writer orchestrator
        b.append("side_tasks", {"event": "queued"}, agent="a1_bu")


def test_failed_side_task_is_note_not_blocked(store):
    b = _new(store, "P-0")
    pstatus.update(b, "AWAIT_G1")
    t = side_tasks.add(Blackboard.resume("P-0", store), "b1_ba", "điều tra")
    probe = Probe(delay=0, fail="side:" + t["id"])
    s = scheduler.Scheduler(store, sender=probe, cfg=_cfg(), log=lambda *_: None)
    try:
        s.run_once(timeout=30)
    finally:
        s.close()
    rec = side_tasks.tasks(Blackboard.load("P-0", store))[0]
    assert rec["status"] == "failed" and "mạng lỗi" in rec["reason"]
    assert not _trace(store, "P-0", "BLOCKED")
    res = [e for e in _trace(store, "P-0", "RESULT") if e.get("side_task")]
    assert res and res[0]["ok"] is False


def test_blocked_project_not_rerun_until_store_changes(store):
    _new(store, "P-0")
    probe = Probe(delay=0, fail="a1_bu")
    s = scheduler.Scheduler(store, sender=probe, cfg=_cfg(), log=lambda *_: None)
    try:
        assert s.run_once(timeout=30)["outcomes"]["P-0"]["stop"] == "BLOCKED"
        n = len(probe.order)
        assert s.scan() == 0 and len(probe.order) == n          # không chạy lại / không spam BLOCKED
    finally:
        s.close()


def test_escalated_and_unsigned_gate_skipped_no_signing_path(store):
    a, b = _new(store, "P-A"), _new(store, "P-B")
    pstatus.update(a, "AWAIT_G1")
    pstatus.update(b, "ESCALATED")
    s = scheduler.Scheduler(store, sender=mock.sender, cfg=_cfg(), log=lambda *_: None)
    try:
        assert s.scan() == 0
    finally:
        s.close()
    assert not Blackboard.load("P-A", store).is_signed("G1") and _state(store, "P-B") == "ESCALATED"
    for f in ("src/ops/scheduler.py", "src/ops/side_tasks.py", "src/agents/side_task.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert ".sign(" not in src and "revoke_signature" not in src, f
        assert "import escalation" not in src and "escalation.resolve" not in src and "gate.sign" not in src, f
        assert "huddle.run" not in src and "from src.agents import huddle" not in src, f


def test_stale_started_task_reruns_on_first_scan(store):
    b = _new(store, "P-0")
    pstatus.update(b, "AWAIT_G1")
    rb = Blackboard.resume("P-0", store)
    t = side_tasks.add(rb, "b1_ba", "điều tra")
    rb.append("side_tasks", {"event": "started", "id": t["id"], "role": "b1_ba"}, agent="orchestrator")
    s = scheduler.Scheduler(store, sender=mock.sender, cfg=_cfg(), log=lambda *_: None)
    try:
        s.run_once(timeout=30)
    finally:
        s.close()
    assert side_tasks.tasks(Blackboard.load("P-0", store))[0]["status"] == "done"


def test_two_schedulers_refused(store, capsys):
    cfg = scheduler.settings()
    lk = scheduler.FileLock(scheduler.lock_path(store, cfg)).acquire()
    try:
        with pytest.raises(scheduler.LockBusy):
            scheduler.FileLock(scheduler.lock_path(store, cfg)).acquire()
        assert scheduler.main(["run", "--once", "--mock", "--db", str(store.path)]) == 3
        assert "scheduler khác đang chạy" in capsys.readouterr().err
    finally:
        lk.release()
    _new(store, "P-0")
    assert scheduler.main(["run", "--once", "--mock", "--db", str(store.path)]) == 0
    assert _state(store, "P-0") == "AWAIT_G1"
    assert scheduler.main(["status", "--db", str(store.path)]) == 0
    assert "không chạy" in capsys.readouterr().out


def test_cli_task_add_and_list(store, capsys):
    _new(store, "P-0")
    db = str(store.path)
    assert project.main(["task", "add", "--id", "P-0", "--role", "b4_tech_lead", "--title", "API đối tác",
                         "--db", db]) == 0
    assert "Đã xếp hàng S-" in capsys.readouterr().out
    assert project.main(["task", "add", "--id", "P-0", "--role", "khong_co", "--title", "x", "--db", db]) == 1
    assert project.main(["task", "add", "--id", "P-X", "--role", "b4_tech_lead", "--title", "x", "--db", db]) == 1
    assert project.main(["task", "list", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "b4_tech_lead" in out and "queued" in out and "API đối tác" in out
    t = side_tasks.tasks(Blackboard.load("P-0", store))[0]
    assert t["kind"] == "side" and t["via"] == "cli" and "confirmed_by" not in {k for k, v in t.items() if v}


def test_config_validation(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/scheduler.yaml").write_text("scheduler: {max_main_per_role: 0, max_side_per_role: 1, "
                                                    "max_concurrent_calls: 1, max_projects: 1, poll_s: 1, "
                                                    "lock_file: x}\n")
    from src.common import config
    with pytest.raises(config.ConfigError):
        scheduler.settings(tmp_path)
    assert scheduler.settings()["max_main_per_role"] == 1
