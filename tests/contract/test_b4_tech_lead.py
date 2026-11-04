"""Contract test b4_tech_lead — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import b4_tech_lead as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "b4_tech_lead", None
SAMPLE_IN = json.loads('{"stories": [{"as_a": "khách", "i_want": "đăng nhập", "so_that": "xem số dư", "AC": ["Sai mật khẩu 5 lần → khoá 15 phút"]}]}')
SAMPLE_OUT = json.loads('{"architecture": {"style": "modular monolith"}, "adr": [{"decision": "PostgreSQL", "options_rejected": ["MongoDB"], "reason": "giao dịch"}], "task_backlog": [{"id": "T1", "story_ref": "ST1", "spec": "API login", "AC": ["AC1"], "deps": [], "size": "S", "risk_level": "normal"}]}')
CTX = {"project_id": "p-b4_tech_lead"}


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


def test_always_opus_and_risk_enum():
    seen = []
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT), seen))
    assert res["tier"] == "opus"
    bad = json.loads(json.dumps(SAMPLE_OUT))
    bad["task_backlog"][0]["risk_level"] = "extreme"
    assert agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(bad)))["error"] == "SCHEMA_FAIL"
