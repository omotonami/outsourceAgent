"""src/governance/confirm: một danh sách "không phải người" cho mọi kênh xác nhận."""
import pytest

from src.agents import e2_cos
from src.blackboard.store import Blackboard, PermissionDenied
from src.factory import interview
from src.governance import confirm
from src.orchestrator.autonomy import Autonomy, AutonomyRefused

MACHINES = ["c1_dev", "orchestrator", "orchestrator.dispatcher", "dispatcher", "gateway", "huddle",
            "e2_cos", "factory_interviewer", "intake", "auto", "auto-L3", "AUTO-x"]


@pytest.mark.parametrize("who", MACHINES)
def test_machine_ids_rejected_everywhere(who, tmp_path):
    assert confirm.is_machine(who) and not confirm.is_human(who)
    with pytest.raises(ValueError):
        interview.confirm({}, confirmed_by=who)
    with pytest.raises(PermissionDenied):
        e2_cos.confirm("lead_brief", {}, confirmed_by=who)
    with pytest.raises(PermissionDenied):
        Blackboard("p").sign("G1", by=who, via="gate_ui")
    ax = Autonomy(state_path=tmp_path / "s.json", reviews_log=tmp_path / "r.jsonl",
                  feedback_log=tmp_path / "f.jsonl", regression_log=tmp_path / "g.jsonl")
    with pytest.raises(AutonomyRefused):
        ax.promote("a1_bu", "presales", approved_by=who)


@pytest.mark.parametrize("who", ["", "   ", None])
def test_empty_is_not_human(who):
    assert not confirm.is_human(who)


def test_gate_ui_signs_but_is_not_a_confirming_human():
    b = Blackboard("p")
    b.sign("G1", by="gate_ui")                       # kênh ký gate hợp lệ
    assert b.is_signed("G1")
    assert not confirm.is_human("gate_ui")           # nhưng không phải người xác nhận
    with pytest.raises(ValueError):
        interview.confirm({}, confirmed_by="gate_ui")


def test_require_human_strips_and_extra():
    assert confirm.require_human("  lan ", ValueError, "x {who!r}") == "lan"
    with pytest.raises(ValueError, match="'bob'"):
        confirm.require_human("bob", ValueError, "không phải người: {who!r}", extra={"bob"})


def test_payload_hash_stable_and_sensitive():
    a = confirm.payload_hash({"b": 1, "a": [1, 2]})
    assert a == confirm.payload_hash({"a": [1, 2], "b": 1}) and a.startswith("sha256:")
    assert a != confirm.payload_hash({"a": [2, 1], "b": 1})
    assert interview.answers_hash is confirm.payload_hash
