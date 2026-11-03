"""Fix (a): depth guard theo project_id — dự án khác vẫn chạy; worker (và thread con) không tạo Dispatcher."""
import concurrent.futures as cf
import threading

import pytest

from src.blackboard.store import Blackboard
from src.orchestrator.dispatcher import DepthExceeded, Dispatcher, registry


def _board(pid, *ids):
    b = Blackboard(pid)
    b.write("task_backlog", [{"id": i, "story_ref": "S1", "spec": f"làm {i}", "AC": [f"AC {i}"], "deps": [],
                              "size": "S", "risk_level": "normal", "owns": [i.lower()]} for i in ids],
            agent="b4_tech_lead")
    return b


def _ok(contract, h):
    return {"ok": True}


@pytest.fixture
def busy_project():
    """Dự án p-depth-A có 1 worker đang chạy (chờ release) trong thread nền (không phải dòng worker)."""
    started, release, out = threading.Event(), threading.Event(), {}

    def hold(contract, h):
        started.set()
        release.wait(5)
        return {"ok": True}
    t = threading.Thread(target=lambda: out.setdefault("res", Dispatcher(_board("p-depth-A", "A1"), hold).run()))
    t.start()
    assert started.wait(5)
    yield "p-depth-A"
    release.set()
    t.join(5)
    assert out["res"]["done"] == ["A1"]


def test_other_project_runs_while_one_has_live_workers(busy_project):
    assert registry.active(busy_project) == 1
    res = Dispatcher(_board("p-depth-B", "B1", "B2"), _ok).run()      # trước fix: DepthExceeded
    assert sorted(res["done"]) == ["B1", "B2"]


def test_same_project_still_blocked(busy_project):
    with pytest.raises(DepthExceeded, match=busy_project):
        Dispatcher(_board(busy_project, "X"), _ok)


def test_worker_and_its_threads_cannot_create_any_dispatcher():
    caught = []

    def nested(contract, h):
        def try_create(tag):
            try:
                Dispatcher(_board("p-depth-other", "Z"), _ok)
            except DepthExceeded:
                caught.append(tag)
        try_create("worker")
        t = threading.Thread(target=lambda: try_create("thread"))
        t.start()
        t.join()
        with cf.ThreadPoolExecutor(1) as ex:                       # thread cháu qua executor
            ex.submit(lambda: threading.Thread(target=lambda: try_create("pool")).start()).result()
        return {"ok": True}
    res = Dispatcher(_board("p-depth-C", "C1"), nested).run()
    assert res["done"] == ["C1"]
    deadline = threading.Event()
    for _ in range(100):
        if len(caught) == 3:
            break
        deadline.wait(0.01)
    assert sorted(caught) == ["pool", "thread", "worker"]


def test_counter_released_after_run():
    Dispatcher(_board("p-depth-D", "D1"), _ok).run()
    assert registry.active("p-depth-D") == 0
    Dispatcher(_board("p-depth-D", "D2"), _ok)                      # tạo lại được
