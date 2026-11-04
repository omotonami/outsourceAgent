"""Contract test a1_bu — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import a1_bu as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "a1_bu", 'business_context'
SAMPLE_IN = json.loads('{"project_id": "p1", "lead_brief": {"client": "X", "summary": "ví điện tử"}}')
SAMPLE_OUT = json.loads('{"business_context": {"goal": "ví"}, "domain": "fintech", "modules": [{"name": "KYC", "desc": "eKYC", "label": "ASSUMPTION"}], "compliance_flags": ["PDPA"], "ambiguities": ["Nhà cung cấp FX?", "SLA?"]}')
CTX = {"project_id": "p-a1_bu"}


def _fake(text, seen=None):
    def send(model, system, messages, max_tokens):
        if seen is not None:
            seen.append({"model": model, "system": system})
        return text, {"total_tokens": 10}
    return send


def test_invalid_input_rejected():
    res = agent.run(Blackboard("p"), None, CTX, {"__not_in_schema__": 1}, sender=_fake("{}"))
    assert res["error"] == "INVALID_INPUT" and res["tier"]


def test_bad_output_escalates():
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake("not json"))
    assert res["error"] == "SCHEMA_FAIL" and res["escalate"] is True and res["tier"]


def test_prompt_from_spec_with_skill_slot_filled():
    seen = []
    agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT), seen))
    assert "{SKILL_GUIDANCE}" not in seen[0]["system"] and "MODULE 4" in seen[0]["system"]


def test_run_writes_own_zone_offline():
    b = Blackboard("p")
    res = agent.run(b, None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT)))
    assert res.get("ok"), res
    assert b.get(ZONE) == res["data"]


def test_zone_owner_only():
    with pytest.raises(PermissionDenied):
        Blackboard("p").write(ZONE, SAMPLE_OUT, agent="not_" + ID)


def test_ambiguities_go_to_risk_register():
    b = Blackboard("p")
    agent.run(b, None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT)))
    risks = b.get("risk_register")
    assert [r["risk"] for r in risks] == SAMPLE_OUT["ambiguities"]
    assert all(r["source_agent"] == ID and r["status"] == "open" for r in risks)


def test_module_label_enum_enforced():
    bad = {**SAMPLE_OUT, "modules": [{"name": "KYC", "desc": "x", "label": "GUESS"}]}
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(bad)))
    assert res["error"] == "SCHEMA_FAIL"
