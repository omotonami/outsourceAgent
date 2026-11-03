"""H2 Build Dispatcher — worker mock, offline, không API key."""
import threading, time
import pytest
from jsonschema import validate
from src.blackboard.store import Blackboard
from src.orchestrator import dispatcher as dsp
from src.orchestrator.dispatcher import Dispatcher, CycleError, DepthExceeded
from src.orchestrator.state_machine import Orchestrator, TransitionRefused
from src.tools.integrations import repo as git


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


def _board(tasks):
    b = Blackboard("p-h2")
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
    res = Dispatcher(b, w, max_parallel=3, phase_budget=None).run()
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
    res = Dispatcher(b, w, max_parallel=3, phase_budget=None).run()
    assert len(res["done"]) == 8
    assert w.peak == 3 and res["max_concurrency"] == 3


def test_owns_never_overlap_while_running():
    tasks = [_t("A", owns=["src/x.py"]), _t("B", owns=["src/x.py"]), _t("C", owns=["src/"]),
             _t("D", owns=["docs/a.md"])]
    b, w = _board(tasks), Recorder(sleep=0.05)
    Dispatcher(b, w, max_parallel=4, phase_budget=None).run()
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
    Dispatcher(b, w, max_parallel=3, phase_budget=None).run()
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
        Dispatcher(b, w, phase_budget=None).run()
    assert not w.contracts and not b.get("trace_log")


def test_over_budget_stops_spawning():
    tasks = [_t(f"B{i}", owns=[f"f{i}"]) for i in range(6)]
    b, w = _board(tasks), Recorder(tokens=100)
    res = Dispatcher(b, w, max_parallel=1, phase_budget=250, worker_tokens=100).run()
    assert res["spawned"] == 2 and len(w.contracts) == 2
    assert res["escalation"] == "vượt budget"
    assert len(res["pending"]) == 4
    assert any("vượt budget" in r["risk"] for r in b.get("risk_register"))
    assert any(e["task_id"] is None for e in _events(b, "BLOCKED"))
    assert all(c["budget"] == {"tokens": 100} for c in w.contracts)


def test_never_merged_and_g3_still_required():
    b, w = _board(DAG6), Recorder()
    Dispatcher(b, w, max_parallel=3, phase_budget=None).run()
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
    Dispatcher(b, bad, phase_budget=None).run()
    assert seen == ["merged", "await_merge"]
    with pytest.raises(PermissionError):
        Dispatcher(b, bad, phase_budget=None)._set_status("A", "merged")


def test_retry_then_blocked_and_dependents_blocked():
    tasks = [_t("A", owns=["a"]), _t("B", owns=["b"]), _t("C", ["A"], ["c"])]
    b, w = _board(tasks), Recorder(sleep=0.0, fail={"A": 99, "B": 1})
    res = Dispatcher(b, w, max_parallel=2, phase_budget=None).run()
    assert w.calls["A"] == dsp.escalation.MAX_LOOPS  # retry tối đa max_build_loops
    assert w.calls["B"] == 2 and "B" in res["done"]
    assert set(res["blocked"]) == {"A", "C"}
    assert [c["trigger"] for c in w.contracts if c["task_id"] == "A"] == ["fan-out"] + ["retry-fresh"] * 2
    assert b.get("build_results")["all_checks_pass"] is False


def test_depth_one_enforced():
    caught = []

    def nested(contract, h):
        try:
            Dispatcher(Blackboard("x"), lambda c, h: {"ok": True}, phase_budget=None)
        except DepthExceeded:
            caught.append("init")
        return {"ok": True}
    Dispatcher(_board([_t("A")]), nested, phase_budget=None).run()
    assert caught == ["init"]


def test_ttl_kill_blocks_task():
    def slow(contract, h):
        h.cancelled.wait(2)
        return {"ok": True}
    b = _board([_t("A", owns=["a"]), _t("B", ["A"], ["b"])])
    res = Dispatcher(b, slow, ttl=0.1, phase_budget=None).run()
    assert len(_events(b, "KILL")) == 1
    assert set(res["blocked"]) == {"A", "B"}


def test_invalid_contract_not_spawned():
    b, w = _board([_t("A", owns=["a"])]), Recorder()
    d = Dispatcher(b, w, phase_budget=None, owns_of=lambda t: [])  # owns rỗng → sai schema
    res = d.run()
    assert not w.contracts and "A" in res["blocked"]


def test_each_task_gets_own_worktree(tmp_path):
    r = git.init(tmp_path / "repo")
    seen = {}

    def wk(contract, h):
        seen[contract["task_id"]] = contract["input_slice"]["worktree"]
        return {"ok": True}
    b = _board([_t("T1", owns=["a"]), _t("T2", owns=["b"])])
    res = Dispatcher(b, wk, phase_budget=None, repo=r).run()
    assert len(set(seen.values())) == 2
    for tid, path in seen.items():
        assert git._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path).strip() == f"task/{tid}"
    assert res["worktrees"] == seen
