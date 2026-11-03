"""Contract test a3_proposal — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import a3_proposal as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "a3_proposal", 'proposal'
SAMPLE_IN = json.loads('{"estimate": {"assumptions": ["Có sandbox eKYC"], "exclusions": ["Native app"]}}')
SAMPLE_OUT = json.loads('{"proposal_doc": "# Đề xuất", "assumptions_section": "Có sandbox eKYC", "exclusions_section": "Native app", "review_notes": ["ok"]}')
CTX = {"project_id": "p-a3_proposal"}


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


def test_coverage_computed_and_always_needs_human():
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT)))
    assert res["data"]["needs_human"] is True
    assert res["data"]["assumption_coverage"]["ratio"] == 1.0
