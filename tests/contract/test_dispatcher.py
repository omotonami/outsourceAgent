"""H2 Build Dispatcher — worker mock, offline, không API key."""
import threading, time
from pathlib import Path
import pytest
from jsonschema import validate
from src.blackboard.store import Blackboard
from src.orchestrator import dispatcher as dsp
from src.orchestrator.dispatcher import Dispatcher, CycleError, DepthExceeded
from src.orchestrator.state_machine import Orchestrator, TransitionRefused
from src.tools.integrations import repo as git
from src.gateway import budget, llm_client
from src.blackboard.store import PermissionDenied


def _t(i, deps=(), owns=None):
    t = {"id": i, "story_ref": "S1", "spec": f"làm {i}", "AC": [f"AC {i}"], "deps": list(deps),
         "size": "S", "risk_level": "normal"}
    if owns is not None:
        t["owns"] = owns
    return t


# DAG 6 task / 3 tầng: (T1,T2) → (T3,T4) → (T5,T6)
DAG6 = [_t("T1", owns=["a"]), _t("T2", owns=["b"]),
        _t("T3", ["T1"], ["c"]), _t("T4", ["T1", "T2"], ["d"]),
        _t("T5", ["T3"], ["e"]), _t("T6", ["T3", "T4"], ["f"])]


def _board(tasks, pid="p-h2"):
    b = Blackboard(pid)
    b.write("task_backlog", [dict(t) for t in tasks], agent="b4_tech_lead")
    return b


class Recorder:
    """Worker mock đo concurrency thật + ghi khoảng thời gian chạy."""

    def __init__(self, sleep=0.05, fail=None, tokens=None):
        self.lock = threading.Lock()
        self.live = self.peak = 0
        self.spans, self.contracts = {}, []
        self.sleep, self.fail, self.tokens = sleep, fail or {}, tokens
        self.calls = {}

    def __call__(self, contract, h):
        validate(contract, dsp.CONTRACT_SCHEMA)
        tid = contract["task_id"]
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
            self.contracts.append(contract)
            self.calls[tid] = self.calls.get(tid, 0) + 1
            n = self.calls[tid]
        start = time.monotonic()
        if self.tokens:          # tiêu token thật qua gateway (sổ chung) bằng sender mock
            llm_client.call("c1_dev", "sonnet", "", [{"role": "user", "content": ""}],
                            h.board.project_id, "build", max_tokens=60,
                            sender=lambda m, s, msg, mt: ("{}", {"total_tokens": self.tokens}))
        h.report("testing")
        time.sleep(self.sleep)
        h.report("qc")
        with self.lock:
            self.live -= 1
        self.spans.setdefault(tid, []).append((start, time.monotonic()))
        ok = n > self.fail.get(tid, 0)
        return {"ok": ok, "reason": None if ok else "test fail", "tokens_used": self.tokens}


def _events(b, kind):
    return [e for e in (b.get("trace_log") or []) if e["event"] == kind]


def test_dag6_order_parallel_and_trace():
    b, w = _board(DAG6), Recorder()
    res = Dispatcher(b, w, max_parallel=3).run()
    assert sorted(res["done"]) == [t["id"] for t in DAG6] and not res["blocked"]
    deps = {t["id"]: t["deps"] for t in DAG6}
    for tid, d in deps.items():
        start = w.spans[tid][0][0]
        assert all(start >= w.spans[x][0][1] for x in d), f"{tid} chạy trước dep"
    assert w.peak >= 2  # thật sự song song
    assert len(_events(b, "SPAWN")) == 6 and len(_events(b, "RESULT")) == 6
    assert all(e["source_agent"] == "orchestrator" for e in b.get("trace_log"))
    assert all(t["status"] == "await_merge" for t in b.get("task_backlog"))


def test_never_exceeds_max_parallel():
    tasks = [_t(f"W{i}", owns=[f"f{i}"]) for i in range(8)]
    b, w = _board(tasks), Recorder(sleep=0.05)
    res = Dispatcher(b, w, max_parallel=3).run()
    assert len(res["done"]) == 8
    assert w.peak == 3 and res["max_concurrency"] == 3


def test_owns_never_overlap_while_running():
    tasks = [_t("A", owns=["src/x.py"]), _t("B", owns=["src/x.py"]), _t("C", owns=["src/"]),
             _t("D", owns=["docs/a.md"])]
    b, w = _board(tasks), Recorder(sleep=0.05)
    Dispatcher(b, w, max_parallel=4).run()
    owns = {t["id"]: t["owns"] for t in tasks}
    ids = list(owns)
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            if dsp._overlap(owns[x], owns[y]):
                (s1, e1), (s2, e2) = w.spans[x][0], w.spans[y][0]
                assert e1 <= s2 or e2 <= s1, f"{x} và {y} trùng owns mà chạy cùng lúc"
    assert w.peak == 2  # chỉ D chạy song song được với một task src/


def test_default_owns_is_exclusive():
    tasks = [_t("A"), _t("B"), _t("C")]  # B4 không khai owns
    b, w = _board(tasks), Recorder(sleep=0.02)
    Dispatcher(b, w, max_parallel=3).run()
    assert w.peak == 1


@pytest.mark.parametrize("tasks", [
    [_t("A", ["B"]), _t("B", ["A"])],
    [_t("A", ["C"]), _t("B", ["A"]), _t("C", ["B"])],
    [_t("A", ["ZZZ"])],
    [_t("A"), _t("A")],
])
def test_bad_dag_rejected(tasks):
    b, w = _board(tasks), Recorder()
    with pytest.raises(CycleError):
        Dispatcher(b, w).run()
    assert not w.contracts and not b.get("trace_log")


@pytest.fixture
def cap(monkeypatch):
    def set_cap(n):
        monkeypatch.setitem(budget._CFG["per_project_tokens"], "build", n)
        monkeypatch.setitem(budget._CFG, "block_auto_escalate_at_ratio", 1.0)
    yield set_cap
    budget.reset()


def test_over_budget_stops_spawning(cap):
    cap(250)
    tasks = [_t(f"B{i}", owns=[f"f{i}"]) for i in range(6)]
    b, w = _board(tasks, pid="p-h2-budget"), Recorder(tokens=90)
    res = Dispatcher(b, w, max_parallel=1, worker_tokens=100).run()
    assert res["spawned"] == 2 and len(w.contracts) == 2
    assert res["escalation"] == "vượt budget"
    assert len(res["pending"]) == 4
    assert any("vượt budget" in r["risk"] for r in b.get("risk_register"))
    assert any(e["task_id"] is None for e in _events(b, "BLOCKED"))
    assert all(c["budget"] == {"tokens": 100} for c in w.contracts)


def test_never_merged_and_g3_still_required():
    b, w = _board(DAG6), Recorder()
    Dispatcher(b, w, max_parallel=3).run()
    assert not any(t["status"] == "merged" for t in b.get("task_backlog"))
    assert b.get("build_results")["all_checks_pass"] is True
    o = Orchestrator(b, state="BUILD")
    assert o.advance() == "AWAIT_G3"
    with pytest.raises(TransitionRefused):
        o.advance()  # G3 chưa ký → không đi tiếp


def test_worker_cannot_report_merged_or_await_merge():
    seen = []

    def bad(contract, h):
        for s in ("merged", "await_merge"):
            try:
                h.report(s)
            except PermissionError:
                seen.append(s)
        return {"ok": True}
    b = _board([_t("A")])
    Dispatcher(b, bad).run()
    assert seen == ["merged", "await_merge"]
    with pytest.raises(PermissionError):
        Dispatcher(b, bad)._set_status("A", "merged")


def test_retry_then_blocked_and_dependents_blocked():
    tasks = [_t("A", owns=["a"]), _t("B", owns=["b"]), _t("C", ["A"], ["c"])]
    b, w = _board(tasks), Recorder(sleep=0.0, fail={"A": 99, "B": 1})
    res = Dispatcher(b, w, max_parallel=2).run()
    assert w.calls["A"] == dsp.escalation.MAX_LOOPS  # retry tối đa max_build_loops
    assert w.calls["B"] == 2 and "B" in res["done"]
    assert set(res["blocked"]) == {"A", "C"}
    assert [c["trigger"] for c in w.contracts if c["task_id"] == "A"] == ["fan-out"] + ["retry-fresh"] * 2
    assert b.get("build_results")["all_checks_pass"] is False


def test_depth_one_enforced():
    caught = []

    def nested(contract, h):
        try:
            Dispatcher(Blackboard("x"), lambda c, h: {"ok": True})
        except DepthExceeded:
            caught.append("init")
        return {"ok": True}
    Dispatcher(_board([_t("A")]), nested).run()
    assert caught == ["init"]


def test_ttl_kill_blocks_task():
    def slow(contract, h):
        h.cancelled.wait(2)
        return {"ok": True}
    b = _board([_t("A", owns=["a"]), _t("B", ["A"], ["b"])])
    res = Dispatcher(b, slow, ttl=0.1).run()
    assert len(_events(b, "KILL")) == 1
    assert set(res["blocked"]) == {"A", "B"}


def test_invalid_contract_not_spawned():
    b, w = _board([_t("A", owns=["a"])]), Recorder()
    d = Dispatcher(b, w, owns_of=lambda t: [])  # owns rỗng → sai schema
    res = d.run()
    assert not w.contracts and "A" in res["blocked"]


def test_each_task_gets_own_worktree(tmp_path):
    r = git.init(tmp_path / "repo")
    seen = {}

    def wk(contract, h):
        seen[contract["task_id"]] = contract["input_slice"]["worktree"]
        return {"ok": True}
    b = _board([_t("T1", owns=["a"]), _t("T2", owns=["b"])])
    res = Dispatcher(b, wk, repo=r).run()
    assert len(set(seen.values())) == 2
    for tid, path in seen.items():
        assert git._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path).strip() == f"task/{tid}"
    assert res["worktrees"] == seen


# ================= Review fixes H2 =================
# #2 Depth guard không lách được bằng thread mới
def test_depth_guard_blocks_new_thread_from_worker():
    caught = []

    def sneaky(contract, h):
        def inner():
            try:
                Dispatcher(Blackboard("x"), lambda c, h: {"ok": True})
            except DepthExceeded:
                caught.append("thread-init")
            try:
                dsp.Dispatcher.run(object.__new__(Dispatcher))
            except DepthExceeded:
                caught.append("thread-run")
        t = threading.Thread(target=inner)
        t.start()
        t.join()
        return {"ok": True}
    res = Dispatcher(_board([_t("A")]), sneaky).run()
    assert caught == ["thread-init", "thread-run"] and res["done"] == ["A"]
    Dispatcher(Blackboard("y"), lambda c, h: {"ok": True})      # xong worker → main tạo lại được


# #3 Zombie: run() sau tôn trọng owns của zombie còn sống; thoát tiến trình không treo
def test_zombie_owns_respected_across_runs():
    release = threading.Event()

    def stubborn(contract, h):          # phớt lờ cancelled
        release.wait(10)
        return {"ok": True}
    pid = "p-zombie"
    b1 = _board([_t("Z", owns=["src/core"])], pid=pid)
    r1 = Dispatcher(b1, stubborn, ttl=0.05, kill_grace_s=0.05).run()
    assert "Z" in r1["blocked"] and len(dsp.live_zombies(pid)) == 1
    try:
        w = Recorder(sleep=0)
        b2 = _board([_t("X", owns=["src/core/a.py"]), _t("Y", owns=["docs"])], pid=pid)
        r2 = Dispatcher(b2, w, max_parallel=3, kill_grace_s=0.05).run()
        assert "Y" in r2["done"] and "X" in r2["pending"]       # X trùng owns với zombie → chờ
        assert w.calls.get("X") is None
        assert r2["escalation"] == "worker bị KILL chưa dừng, owns còn bị giữ"
        assert dsp.live_zombies("p-other") == []                # zombie chỉ chặn đúng dự án
    finally:
        release.set()
    for _ in range(200):
        if not dsp.live_zombies(pid):
            break
        time.sleep(0.01)
    r3 = Dispatcher(b2, Recorder(sleep=0), kill_grace_s=0.05).run()
    assert "X" in r3["done"]


def test_zombie_does_not_hang_process_exit():
    import subprocess, sys, textwrap
    from pathlib import Path
    code = textwrap.dedent("""
        import time
        from src.blackboard.store import Blackboard
        from src.orchestrator.dispatcher import Dispatcher
        b = Blackboard("p-exit")
        b.write("task_backlog", [{"id": "A", "spec": "x", "AC": ["a"], "deps": [], "risk_level": "normal"}],
                agent="b4_tech_lead")
        def forever(c, h):
            while True:
                time.sleep(0.05)
        print(Dispatcher(b, forever, ttl=0.1, kill_grace_s=0.1).run()["blocked"])
    """)
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[2],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "KILL" in r.stdout


# #4 Budget: dùng sổ gateway, 3 worker song song không vượt trần
def test_parallel_workers_share_gateway_ledger(cap):
    cap(1000)
    pid = "p-h2-cap"

    def spender(contract, h):
        for _ in range(3):
            llm_client.call("c1_dev", "sonnet", "", [{"role": "user", "content": ""}], pid, "build",
                            max_tokens=200, sender=lambda m, s, msg, mt: (time.sleep(0.03) or "{}",
                                                                           {"total_tokens": 231}))
        return {"ok": True}
    tasks = [_t(f"P{i}", owns=[f"p{i}"]) for i in range(3)]
    res = Dispatcher(_board(tasks, pid=pid), spender, max_parallel=3, worker_tokens=None).run()
    assert budget.used(pid, "build") <= 1000
    assert budget.reserved(pid, "build") == 0
    assert res["escalation"] == "vượt budget" and res["used_tokens"] == budget.used(pid, "build")


# #10 Chạy lại dùng lại worktree/nhánh task/<id>
def test_rerun_reuses_existing_worktree(tmp_path):
    r = git.init(tmp_path / "repo")
    seen = []

    def wk(contract, h):
        seen.append(contract["input_slice"]["worktree"])
        return {"ok": True}
    Dispatcher(_board([_t("T1", owns=["a"])]), wk, repo=r).run()
    res = Dispatcher(_board([_t("T1", owns=["a"])]), wk, repo=r).run()     # attempt mới, run mới
    assert res["done"] == ["T1"] and not res["blocked"]
    assert seen[0] == seen[1]
    assert git.cleanup_task_worktrees(r, force=True, delete_branch=True) == [Path(seen[0])]


# #11 TOCTOU: KILL xảy ra giữa lần kiểm cancelled đầu và lúc ghi
def test_report_rechecks_cancel_inside_lock():
    class Flaky(threading.Event):
        n = 0

        def is_set(self):
            self.n += 1
            return self.n > 1          # lần kiểm đầu: chưa huỷ; ngay sau đó bị KILL
    b = _board([_t("A")])
    d = Dispatcher(b, lambda c, h: {"ok": True})
    d._set_status("A", BLOCKED_ := "blocked", blocked_reason="KILL: quá ttl")
    h = dsp.WorkerHandle(d, "A")
    h.cancelled = Flaky()
    h.report("qc")
    assert b.get("task_backlog")[0]["status"] == BLOCKED_


# #12 default_pipeline: Opus vẫn fail → blocked ngay (không retry thêm)
def test_default_pipeline_opus_fail_escalates():
    calls = []

    def bad(model, system, messages, max_tokens):
        calls.append(model)
        return "not json", {"total_tokens": 1}
    t = {**_t("H", owns=["h"]), "risk_level": "high"}
    res = Dispatcher(_board([t]), dsp.default_pipeline(sender=bad)).run()
    assert res["blocked"]["H"].startswith("Opus vẫn fail")
    assert len(calls) == 2                       # 1 lần + 1 retry schema trong runtime, không vòng build nữa


# #13 Worker nhận board proxy: không sign/revoke, chỉ ghi zone của agent con
def test_worker_board_proxy_cannot_sign_or_write_foreign_zone():
    seen = {}

    def wk(contract, h):
        for name in ("sign", "revoke_signature"):
            try:
                getattr(h.board, name)("G3", by="gate_ui")
            except AttributeError:
                seen[name] = "AttributeError"
        try:
            h.board.write("task_backlog", [], agent="orchestrator")
        except PermissionDenied:
            seen["task_backlog"] = "denied"
        try:
            h.board.write("gate_signatures", {}, agent="c1_dev")
        except PermissionDenied:
            seen["gate_signatures"] = "denied"
        try:
            h.board._data = {}
        except AttributeError:
            seen["_data"] = "AttributeError"
        h.board.write("build_results", {"dev": "ok"}, agent="c1_dev")
        return {"ok": True}
    b = _board([_t("A")])
    res = Dispatcher(b, wk).run()
    assert res["done"] == ["A"]
    assert seen == {"sign": "AttributeError", "revoke_signature": "AttributeError",
                    "task_backlog": "denied", "gate_signatures": "denied", "_data": "AttributeError"}
    assert not b.is_signed("G3")


# #14 Không default hard-code: thiếu config → lỗi rõ ràng
@pytest.mark.parametrize("key", ["max_parallel", "worker_ttl_s", "kill_grace_s", "worker_tokens"])
def test_missing_dispatcher_config_raises(monkeypatch, key):
    cfg = {k: v for k, v in dsp._BUDGETS["dispatcher"].items() if k != key}
    monkeypatch.setitem(dsp._BUDGETS, "dispatcher", cfg)
    with pytest.raises(dsp.ConfigError, match=key):
        Dispatcher(_board([_t("A")]), lambda c, h: {"ok": True})
