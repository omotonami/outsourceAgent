"""Contract test c3_qc — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import c3_qc as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "c3_qc", None
SAMPLE_IN = json.loads('{"pr_diff": "+def login(): ...", "spec": "API login", "AC": ["AC1"]}')
SAMPLE_OUT = json.loads('{"verdict": "fail", "issues": [{"severity": "Critical", "file": "src/auth.py", "line": 3, "reason": "Không khoá sau 5 lần sai", "fix_hint": "Thêm counter"}]}')
CTX = {"project_id": "p-c3_qc"}


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


def test_run_offline_does_not_write_board():
    b = Blackboard("p")
    res = agent.run(b, None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT)))
    assert res.get("ok"), res
    assert res["data"] == SAMPLE_OUT
    assert b._data == {}


def test_cannot_sign_or_write_gate_zone():
    b = Blackboard("p")
    with pytest.raises(PermissionDenied):
        b.write("gate_signatures", {"G3": True}, agent=ID)
    with pytest.raises(PermissionDenied):
        b.sign("G3", by=ID, via="gate_ui")


def test_verdict_and_severity_enums():
    for bad in ({"verdict": "maybe", "issues": []},
                {"verdict": "pass", "issues": [{"severity": "Blocker", "reason": "x"}]}):
        res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(bad)))
        assert res["error"] == "SCHEMA_FAIL"


def test_high_risk_review_uses_opus():
    res = agent.run(Blackboard("p"), None, {**CTX, "risk_level": "high"}, SAMPLE_IN,
                    sender=_fake(json.dumps(SAMPLE_OUT)))
    assert res["tier"] == "opus"
