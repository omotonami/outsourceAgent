"""Contract test a2_estimation — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import tempfile
import pytest
from src.agents import a2_estimation as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "a2_estimation", 'estimate'
SAMPLE_IN = json.loads('{"project_id": "p1", "modules": [{"name": "KYC", "desc": "eKYC"}]}')
SAMPLE_OUT = json.loads('{"wbs": [{"id": "W1", "item": "KYC onboarding flow", "role": "dev", "size": "M", "effort_md": 999}], "assumptions": ["Có sandbox eKYC"], "exclusions": ["Native app"], "risks": [], "reflection": {"passed": true, "issues": []}}')
CTX = {"project_id": "p-a2_estimation", "domain": "fintech", "blended_rate": 650, "hist_base": tempfile.mkdtemp(prefix="hist-empty-")}


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


def test_llm_never_sets_numbers():
    b = Blackboard("p")
    res = agent.run(b, None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT)))
    w = res["data"]["wbs"][0]
    assert w["effort_md"] != 999 and w["label"] == "ASSUMPTION" and "parametric" in w["basis"]
    assert res["data"]["needs_human"] is True
