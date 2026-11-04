"""Contract test e2_cos (sinh bởi src/factory). Offline: sender mock, không cần API key."""
import json
from src.agents import e2_cos as agent
from src.blackboard.store import Blackboard

ID = "e2_cos"
SAMPLE_IN = json.loads('{"request": "sample", "project_status": {}, "risk_register": [{}], "decision_records": [{}], "trace_summary": [{}]}')
SAMPLE_OUT = json.loads('{"understanding": "sample", "clarifications": ["sample"], "plan": ["sample"], "assignments": [{}], "decisions_needed": ["sample"]}')
CTX = {"project_id": "p-e2_cos"}


def _fake(text, seen=None):
    def send(model, system, messages, max_tokens):
        if seen is not None:
            seen.append(system)
        return text, {"total_tokens": 10}
    return send


def test_run_offline_returns_data_without_writing_board():
    b, seen = Blackboard("p"), []
    res = agent.run(b, None, CTX, SAMPLE_IN, sender=_fake(json.dumps(SAMPLE_OUT), seen))
    assert res.get("ok"), res
    assert res["data"] == SAMPLE_OUT
    assert b._data == {}                         # agent không ghi Blackboard
    assert "{SKILL_GUIDANCE}" not in seen[0] and "MODULE 4" in seen[0]


def test_invalid_input_rejected():
    res = agent.run(Blackboard("p"), None, CTX, {"__not_in_schema__": 1}, sender=_fake("{}"))
    assert res["error"] == "INVALID_INPUT"


def test_bad_output_escalates():
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN, sender=_fake("not json"))
    assert res["error"] == "SCHEMA_FAIL" and res["escalate"] is True and res["tier"]


def test_extra_output_key_rejected():
    res = agent.run(Blackboard("p"), None, CTX, SAMPLE_IN,
                    sender=_fake(json.dumps({**SAMPLE_OUT, "__extra__": 1})))
    assert res["error"] == "SCHEMA_FAIL"
