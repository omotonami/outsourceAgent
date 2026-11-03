"""Contract test b1_ba — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import b1_ba as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "b1_ba", 'requirements'
SAMPLE_IN = json.loads('{"signed_proposal": {"scope": ["KYC"]}}')
SAMPLE_OUT = json.loads('{"epics": ["Onboarding"], "stories": [{"as_a": "khách", "i_want": "đăng nhập", "so_that": "xem số dư", "AC": ["Sai mật khẩu 5 lần → khoá 15 phút"]}], "open_questions": [{"q": "Nhà cung cấp eKYC?", "owner": "client", "due": "2026-11-10"}]}')
CTX = {"project_id": "p-b1_ba"}


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


def test_story_without_ac_rejected():
    bad = {**SAMPLE_OUT, "stories": [{k: v for k, v in SAMPLE_OUT["stories"][0].items() if k != "AC"}]}
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake(json.dumps(bad)))
    assert res["error"] == "SCHEMA_FAIL"
