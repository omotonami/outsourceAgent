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


# ================= Review cuối #6: không gắn cờ vĩnh viễn lên Thread; kiểm theo thời điểm chạy =================
def test_shared_pool_started_from_worker_no_false_depth_for_other_project():
    pool = cf.ThreadPoolExecutor(max_workers=1)          # pool dùng chung (http client, logging...)
    seen = {}

    def w(contract, h):
        seen["thread"] = pool.submit(threading.current_thread).result()   # thread pool start LẦN ĐẦU từ worker
        try:                                                              # trong lúc worker chạy: vẫn chặn
            pool.submit(lambda: Dispatcher(_board("p-depth-E", "E2"), _ok)).result()
        except DepthExceeded:
            seen["inside"] = "DepthExceeded"
        return {"ok": True}
    assert Dispatcher(_board("p-depth-E", "E1"), w).run()["done"] == ["E1"]
    assert seen["inside"] == "DepthExceeded"
    assert not any("lineage" in k for k in vars(seen["thread"]))           # không cờ trên object Thread
    res = pool.submit(lambda: Dispatcher(_board("p-depth-F", "F1"), _ok).run()).result()
    assert res["done"] == ["F1"]                                          # trước fix: DepthExceeded giả
    pool.shutdown()


def test_worker_pool_thread_blocked_same_project_while_worker_runs():
    caught = []

    def w(contract, h):
        with cf.ThreadPoolExecutor(1) as ex:
            try:
                ex.submit(lambda: Dispatcher(_board("p-depth-G", "G2"), _ok)).result()
            except DepthExceeded as e:
                caught.append(str(e))
        return {"ok": True}
    assert Dispatcher(_board("p-depth-G", "G1"), w).run()["done"] == ["G1"]
    assert len(caught) == 1


def test_threads_outside_workers_unchanged():
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    assert "run" not in vars(t) and not any("lineage" in k for k in vars(t))
