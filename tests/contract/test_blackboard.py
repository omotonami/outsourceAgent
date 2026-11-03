import pytest
from src.blackboard.store import Blackboard, PermissionDenied
from src.orchestrator.state_machine import Orchestrator, TransitionRefused


def test_owner_write():
    b = Blackboard("p1")
    with pytest.raises(PermissionDenied):
        b.write("estimate", {}, agent="a3_proposal")


def test_gate_blocks_and_backtrack_revokes():
    b = Blackboard("p1")
    b.write("lead_brief", {"client": "x", "source": "email", "received_at": "2026-10-20"}, agent="intake")
    o = Orchestrator(b)
    o.advance()  # -> PRESALES
    b.write("estimate", {"wbs": [], "effort_md_total": 0, "cost_range": {"low": 0, "base": 0, "high": 0},
                         "assumptions": ["A"], "exclusions": []}, agent="a2_estimation")
    b.write("proposal", {"assumptions_section": "A", "exclusions_section": ""}, agent="a3_proposal")
    o.advance()  # -> AWAIT_G1
    with pytest.raises(TransitionRefused):
        o.advance()  # chưa ký G1
    b.sign("G1", by="sales_lead")
    o.advance()  # -> DISCOVERY
    o.backtrack("PRESALES", reason="đổi scope", by="pm")
    assert not b.is_signed("G1")


def test_estimate_schema_accepts_db_driven_shape():
    """Khoá schema: effort_md null + source object phải hợp lệ (regression)."""
    b = Blackboard("p2")
    est = {"wbs": [
        {"id": "W1", "item": "KYC", "role": "dev", "effort_md": 22.0,
         "source": {"type": "historical", "refs": ["H-fin-01"]}, "label": "FACT"},
        {"id": "W2", "item": "FX", "role": "dev", "effort_md": None,
         "source": {"type": "none", "refs": []}, "label": "ASSUMPTION"}],
        "effort_md_total": 22.0, "cost_range": {"low": 1, "base": 2, "high": 3},
        "assumptions": ["x"], "exclusions": []}
    b.write("estimate", est, agent="a2_estimation")  # không ném là pass
    assert b.get("estimate")["effort_md_total"] == 22.0
