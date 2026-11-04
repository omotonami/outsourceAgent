"""Fix (d): proxy không để lộ board thật qua thuộc tính; không sign / ghi zone khác được."""
import pytest

from src.agents import e2_cos
from src.blackboard.proxy import ReadOnlyBoard, WorkerBoard
from src.blackboard.store import Blackboard, PermissionDenied


def _board():
    b = Blackboard("p-proxy")
    b.write("task_backlog", [{"id": "T1"}], agent="b4_tech_lead")
    return b


def _reachable(obj, depth=2):
    """Mọi object tới được qua thuộc tính KHÔNG phải dunder (kể cả tên mangle _X__y)."""
    seen, frontier = [], [obj]
    for _ in range(depth):
        nxt = []
        for o in frontier:
            for name in dir(o):
                if name.startswith("__") and name.endswith("__"):
                    continue
                try:
                    v = getattr(o, name)
                except AttributeError:
                    continue
                seen.append((name, v))
                nxt.append(v)
        frontier = nxt
    return seen


@pytest.mark.parametrize("make", [lambda b: WorkerBoard(b, ["c1_dev"]), lambda b: ReadOnlyBoard(b, "x"),
                                  lambda b: e2_cos.CosBoard(b)])
def test_proxy_does_not_expose_real_board(make):
    b = _board()
    p = make(b)
    assert not any(v is b for _, v in _reachable(p))
    for name in ("_WorkerBoard__b", "_ReadOnlyBoard__b", "_CosBoard__b", "_b", "board", "_data"):
        assert not hasattr(p, name)
    with pytest.raises(AttributeError):
        p._data = {}                                  # không gắn thêm thuộc tính
    with pytest.raises(AttributeError):
        p.board = b
    assert p.get("task_backlog") == [{"id": "T1"}] and p.project_id == "p-proxy"


def test_worker_board_cannot_sign_or_write_other_zone():
    b = _board()
    p = WorkerBoard(b, ["c1_dev"])
    for name in ("sign", "revoke_signature"):
        assert not hasattr(p, name)
    with pytest.raises(PermissionDenied):
        p.write("task_backlog", [], agent="b4_tech_lead")     # danh tính không phải agent con
    with pytest.raises(PermissionDenied):
        p.write("gate_signatures", {}, agent="c1_dev")        # zone không phải của c1_dev (Blackboard chặn)
    with pytest.raises(PermissionDenied):
        p.append("trace_log", {"event": "X"}, agent="orchestrator")
    p.write("build_results", {"dev": "ok"}, agent="c1_dev")
    assert b.get("build_results") == {"dev": "ok"} and b.get("task_backlog") == [{"id": "T1"}]
    assert not b.is_signed("G3")


def test_read_only_board_denies_every_write():
    b = _board()
    p = ReadOnlyBoard(b, "e2_cos")
    for call in (lambda: p.write("task_backlog", []), lambda: p.append("risk_register", {}),
                 lambda: p.sign("G1", by="lan", via="gate_ui"), lambda: p.revoke_signature("G1", "x")):
        with pytest.raises(PermissionDenied, match="e2_cos chỉ đọc"):
            call()
    assert b.get("task_backlog") == [{"id": "T1"}] and not b.is_signed("G1")
