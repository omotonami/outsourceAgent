"""Contract test d1_soc — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import d1_soc as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "d1_soc", 'security_report'
SAMPLE_IN = json.loads('{"raw_findings": [{"type": "SQLi", "severity": "High", "cwe": "CWE-89", "location": "src/db.py"}]}')
SAMPLE_OUT = json.loads('{"findings": [{"type": "SQLi", "severity": "High", "cwe": "CWE-89", "location": "src/db.py", "remediation": "Dùng parameterized query", "false_positive": false}], "gate": "block"}')
CTX = {"project_id": "p-d1_soc"}


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


def test_gate_enum_and_g4_still_needs_signature():
    from src.orchestrator import gates
    b = Blackboard("p")
    agent.run(b, None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT)))
    ok, _ = gates.g4_signed(b)
    assert not ok                                        # agent không thay được chữ ký G4
    bad = {**SAMPLE_OUT, "gate": "auto-pass"}
    assert agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(bad)))["error"] == "SCHEMA_FAIL"
