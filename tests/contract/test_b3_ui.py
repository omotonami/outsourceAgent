"""Contract test b3_ui — offline (sender mock, không API key), cùng khuôn test do src/factory sinh."""
import json
import pytest
from src.agents import b3_ui as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID, ZONE = "b3_ui", 'ui_system'
SAMPLE_IN = json.loads('{"design_spec": {"screens": [{"id": "S1"}]}}')
SAMPLE_OUT = json.loads('{"tokens": {"color": {"primary": "#0F6E6E"}}, "tokens_css": ":root{--primary:#0F6E6E}", "components": [{"name": "Button", "html": "<button>OK</button>", "states": ["default", "hover"]}], "screens": [{"id": "S1", "html": "<main></main>", "macrostructure": "split-hero"}], "a11y": ["AA"]}')
CTX = {"project_id": "p-b3_ui"}


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
