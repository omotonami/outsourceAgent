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
    b.sign("G1", by="sales_lead", via="gate_ui")
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


# ---------- sign(): chỉ gate_ui (review H2 #13) ----------
@pytest.mark.parametrize("by, via", [
    ("c1_dev", None), ("c1_dev", "gate_ui"), ("orchestrator", "gate_ui"), ("orchestrator.dispatcher", None),
    ("auto-L3", "gate_ui"), ("sales_lead", None), ("sales_lead", "worker"), ("", "gate_ui"),
])
def test_sign_rejects_non_gate_ui(by, via):
    b = Blackboard("p")
    with pytest.raises(PermissionDenied):
        b.sign("G3", by=by, via=via)
    assert not b.is_signed("G3")


def test_sign_accepts_gate_ui_and_human_via_gate_ui():
    b = Blackboard("p")
    b.sign("G1", by="gate_ui")
    b.sign("G2", by="sales_lead", via="gate_ui")
    assert b.is_signed("G1") and b.is_signed("G2")


# Quyết định 2026-11-03 (c): runtime đọc thẳng specs/, không còn bản sao config trong src/
def test_runtime_reads_specs_single_source():
    from pathlib import Path
    import yaml
    from src.blackboard import store
    from src.gateway import router
    root = Path(__file__).parents[2]
    assert store.ZONES_FILE == root / "specs/05_blackboard.yaml"
    assert router.ROUTING_FILE == root / "specs/03_routing.yaml"
    assert not (root / "src/gateway/routing.yaml").exists()
    assert not (root / "src/blackboard/permissions.yaml").exists()
    assert store._PERM == yaml.safe_load(store.ZONES_FILE.read_text(encoding="utf-8"))["zones"]
    assert router._CFG == yaml.safe_load(router.ROUTING_FILE.read_text(encoding="utf-8"))


def test_clarify_answers_zone_in_specs():
    from src.agents import clarify
    b = Blackboard("p1")
    b.write("business_context", {"ambiguities": ["q1", "q2"]}, agent="a1_bu")
    clarify.resolve(b, "q1", "a1")                     # writer 'clarify' theo specs/05_blackboard.yaml
    assert clarify.pending(b) == ["q2"]
    with pytest.raises(PermissionDenied):
        b.write("clarify_answers", [], agent="a1_bu")
