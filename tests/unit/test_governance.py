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


# ================= Review cuối #3: chuẩn hoá danh tính trước khi so =================
VARIANTS = ["Orchestrator", " ORCHESTRATOR ", "c1-dev", "C1.DEV", "c1 dev", "huddle:a1_bu", "AUTO-L3",
            "Auto_l2", "e2-cos", "orchestrator:dispatcher", "Gateway-1", "ｏｒｃｈｅｓｔｒａｔｏｒ"]


@pytest.mark.parametrize("who", VARIANTS)
def test_machine_identity_variants_rejected_everywhere(who, tmp_path):
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
    with pytest.raises(AutonomyRefused):
        ax.record_review("c1_dev", "build_task", "G3", reviewer=who)
    assert not (tmp_path / "r.jsonl").exists()


@pytest.mark.parametrize("who", ["Gate_UI", "gate-ui", " GATE.UI "])
def test_gate_ui_variants_not_human(who):
    assert not confirm.is_human(who)


@pytest.mark.parametrize("who", ["daipc", "lan", "sales_lead", "Nguyễn Văn An", "pm", "tester"])
def test_real_people_still_human(who):
    assert confirm.is_human(who)


def test_promote_extra_agent_normalized(tmp_path):
    ax = Autonomy(state_path=tmp_path / "s.json", reviews_log=tmp_path / "r.jsonl",
                  feedback_log=tmp_path / "f.jsonl", regression_log=tmp_path / "g.jsonl")
    assert confirm.is_machine("My-Bot", extra=["my_bot"])
    with pytest.raises(AutonomyRefused):
        ax.promote("a1_bu", "presales", approved_by="A1-BU")
