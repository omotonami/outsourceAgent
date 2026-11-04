"""Fix (c): ký gate xong mà ghi record_review lỗi → request không lỗi, risk_register có ghi nhận."""
import pytest

app = pytest.importorskip("src.server.app")


class _Broken:
    cfg = {"gate_task_types": {"G3": "build_task"}}

    def __init__(self, exc):
        self.exc = exc

    def record_review(self, *a, **kw):
        raise self.exc


def _at_gate(gid):
    app.S.reset(True)
    app.S.i = next(i for i, s in enumerate(app.S.steps) if s.get("gate") == gid)
    return app.S.i


@pytest.mark.parametrize("exc", [OSError("disk full"), ValueError("mức lạ"), app.ConfigError("thiếu")])
def test_review_log_failure_does_not_fail_gate_request(monkeypatch, exc):
    monkeypatch.setattr(app, "AUTONOMY", _Broken(exc))
    i = _at_gate("G3")
    res = app.gate("approve")
    assert res["signed"] == {"G3": "approved"} and res["i"] == i + 1
    assert app.S.board.is_signed("G3")
    risks = app.S.board.get("risk_register")
    assert len(risks) == 1 and "G3" in risks[0]["risk"] and type(exc).__name__ in risks[0]["risk"]
    assert risks[0]["source_agent"] == "gate_ui" and risks[0]["status"] == "open"
    app.S.reset(True)


def test_unexpected_error_still_raises(monkeypatch):
    monkeypatch.setattr(app, "AUTONOMY", _Broken(RuntimeError("bug")))
    _at_gate("G3")
    with pytest.raises(RuntimeError):
        app.gate("approve")
    app.S.reset(True)


def test_edited_approval_skips_review(monkeypatch):
    monkeypatch.setattr(app, "AUTONOMY", _Broken(OSError("x")))
    _at_gate("G3")
    app.gate("approve", edited=True)
    assert not app.S.board.get("risk_register")
    app.S.reset(True)


# Review cuối #3: reviewer máy (biến thể) → 400, không ký gate, không ghi review
@pytest.mark.parametrize("who", ["Orchestrator", "c1-dev", "huddle:a1_bu", "AUTO-L3"])
def test_gate_rejects_machine_reviewer(monkeypatch, who):
    calls = []

    class _Rec:
        cfg = {"gate_task_types": {"G3": "build_task"}}

        def record_review(self, *a, **kw):
            calls.append(kw)
    monkeypatch.setattr(app, "AUTONOMY", _Rec())
    i = _at_gate("G3")
    res = app.gate("approve", reviewer=who)
    assert getattr(res, "status_code", None) == 400
    assert not app.S.board.is_signed("G3") and app.S.i == i and calls == []
    app.S.reset(True)
