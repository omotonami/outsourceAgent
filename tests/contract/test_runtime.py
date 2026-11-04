"""runtime.run: kết quả lỗi vẫn mang `tier` để escalation nhận ra 'Opus vẫn fail' (review H2 #12)."""
from src.agents import runtime
from src.blackboard.store import Blackboard
from src.orchestrator import escalation


def _fake(text):
    return lambda model, system, messages, max_tokens: (text, {"total_tokens": 1})


def test_schema_fail_returns_tier_and_opus_fail_escalates():
    res = runtime.run("c1_dev", "dev", "build", Blackboard("p"), None,
                      {"project_id": "p-rt", "risk_level": "high"}, {"task": {"id": "T1"}},
                      sender=_fake("not json"))
    assert res["error"] == "SCHEMA_FAIL"
    assert res["tier"] == "opus"                       # risk high → escalate_to opus
    assert escalation.should_escalate(1, res["tier"], failed=True, over_budget=False) == "Opus vẫn fail"


def test_invalid_input_returns_tier():
    res = runtime.run("c1_dev", "dev", "build", Blackboard("p"), None,
                      {"project_id": "p-rt"}, {"bad": 1}, sender=_fake("{}"))
    assert res["error"] == "INVALID_INPUT" and res["tier"] == "sonnet"
