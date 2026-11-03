"""Sổ budget gateway: lock + reserve-trước-gọi, trừ theo thực tế (review H2 #4)."""
import threading
import time

import pytest

from src.gateway import budget, llm_client


@pytest.fixture
def cap(monkeypatch):
    def set_cap(phase, n):
        monkeypatch.setitem(budget._CFG["per_project_tokens"], phase, n)
        monkeypatch.setitem(budget._CFG, "block_auto_escalate_at_ratio", 1.0)
    yield set_cap
    budget.reset()


def _sender(delay=0.05, used=None):
    def send(model, system, messages, max_tokens):
        time.sleep(delay)
        need = max_tokens + llm_client.input_bound(system, messages)
        return "{}", {"total_tokens": need if used is None else used}   # dùng hết cận trên
    return send


def _call(pid, phase, max_tokens, sender):
    return llm_client.call("c1_dev", "sonnet", "s", [{"role": "user", "content": "x"}],
                           pid, phase, max_tokens=max_tokens, sender=sender)


def test_parallel_calls_never_exceed_cap(cap):
    cap("build", 1000)
    pid, ok, refused = "p-budget-par", [], []
    barrier = threading.Barrier(3)

    def worker():
        barrier.wait()
        for _ in range(5):
            try:
                _call(pid, "build", 150, _sender())
                ok.append(1)
            except budget.BudgetExceeded:
                refused.append(1)
                return
    ts = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert budget.used(pid, "build") <= 1000
    assert budget.reserved(pid, "build") == 0
    assert refused and ok


def test_three_simultaneous_reservations_only_two_fit(cap):
    cap("build", 1000)
    pid, results = "p-budget-3", []
    barrier = threading.Barrier(3)

    def worker():
        barrier.wait()
        try:
            _call(pid, "build", 400, _sender(delay=0.1))
            results.append("ok")
        except budget.BudgetExceeded:
            results.append("refused")
    ts = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["ok", "ok", "refused"]
    assert budget.used(pid, "build") <= 1000


def test_commit_uses_actual_and_cancel_on_error(cap):
    cap("build", 1000)
    pid = "p-budget-actual"
    _call(pid, "build", 400, _sender(delay=0, used=7))
    assert budget.used(pid, "build") == 7 and budget.reserved(pid, "build") == 0

    def boom(*a):
        raise RuntimeError("network")
    with pytest.raises(RuntimeError):
        _call(pid, "build", 400, boom)
    assert budget.used(pid, "build") == 7 and budget.reserved(pid, "build") == 0


def test_allocation_draws_without_double_count(cap):
    cap("build", 1000)
    pid = "p-budget-alloc"
    a = budget.reserve(pid, "build", 300)
    assert budget.remaining(pid, "build") == 700
    with budget.use_allocation(a):
        _call(pid, "build", 100, _sender(delay=0, used=50))
        assert budget.used(pid, "build") == 50 and budget.reserved(pid, "build") == 250
        with pytest.raises(budget.BudgetExceeded):        # vượt allocation của worker
            _call(pid, "build", 400, _sender(delay=0))
    budget.release(a)
    assert budget.reserved(pid, "build") == 0 and budget.used(pid, "build") == 50
