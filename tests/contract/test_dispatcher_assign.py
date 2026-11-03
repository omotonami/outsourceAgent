"""Fix (b): Dispatcher phát ASSIGN khi giao task cho worker → Team feed có ASSIGN."""
from src.blackboard.store import Blackboard
from src.orchestrator import team_feed
from src.orchestrator.dispatcher import Dispatcher


def _board(pid, tasks):
    b = Blackboard(pid)
    b.write("task_backlog", tasks, agent="b4_tech_lead")
    return b


def _t(i, **kw):
    return {"id": i, "story_ref": "S1", "spec": f"làm {i}", "AC": [f"AC {i}"], "deps": [], "size": "S",
            "risk_level": "normal", "owns": [i.lower()], **kw}


def _trace(b, kind):
    return [e for e in b.get("trace_log") or [] if e["event"] == kind]


def test_assign_emitted_before_each_spawn():
    b = _board("p-assign", [_t("T1"), _t("T2", agent="c1_dev", type="build_task")])
    res = Dispatcher(b, lambda c, h: {"ok": True}).run()
    assert sorted(res["done"]) == ["T1", "T2"]
    assigns = _trace(b, "ASSIGN")
    assert sorted(e["task_id"] for e in assigns) == ["T1", "T2"]
    for e in assigns:
        assert e["agent"] == "c1_dev" and e["task_type"] == "build_task" and e["trigger"] == "fan-out"
        assert e["by"] == "orchestrator.dispatcher" and e["source_agent"] == "orchestrator"
    log = [(e["event"], e["task_id"]) for e in b.get("trace_log")]
    for tid in ("T1", "T2"):
        assert log.index(("ASSIGN", tid)) < log.index(("SPAWN", tid))


def test_assign_per_attempt_on_retry():
    calls = {"n": 0}

    def flaky(contract, h):
        calls["n"] += 1
        return {"ok": calls["n"] > 1, "reason": "lần đầu fail"}
    b = _board("p-assign-retry", [_t("R")])
    Dispatcher(b, flaky).run()
    assert [e["trigger"] for e in _trace(b, "ASSIGN")] == ["fan-out", "retry-fresh"]


def test_team_feed_shows_assign():
    b = _board("p-assign-feed", [_t("F1")])
    Dispatcher(b, lambda c, h: {"ok": True}).run()
    feed = team_feed.build(b)
    assert feed["counts"]["ASSIGN"] == 1
    ev = next(e for e in feed["events"] if e["type"] == "ASSIGN")
    assert ev["summary"] == "giao F1 cho c1_dev" and ev["actor"] == "orchestrator.dispatcher"


def test_no_assign_when_autonomy_denies():
    class Deny:
        def can_run(self, agent, task_type, trigger):
            return {"allowed": False, "level": "L0", "reason": "autonomy: không cho"}
    b = _board("p-assign-deny", [_t("D1")])
    Dispatcher(b, lambda c, h: {"ok": True}, autonomy=Deny(), trigger="routine").run()
    assert not _trace(b, "ASSIGN") and _trace(b, "BLOCKED")
