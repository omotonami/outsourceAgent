"""B2b UI Designer: role ui, ghi ui_system, hallmark nạp ở lớp team."""
from src.orchestrator import skill_loader
from src.blackboard.store import Blackboard
import pytest

UI = {"tokens":{"color":{"primary":"#0F6E6E"}},"tokens_css":":root{--primary:#0F6E6E}",
      "components":[{"name":"Button","html":"<button>OK</button>","states":["default","hover"]}],
      "screens":[{"id":"S1","html":"<main></main>","macrostructure":"split-hero"}],"a11y":["AA"]}

def test_ui_loads_team_then_core_last():
    sk = skill_loader.load("ui", None, {})
    assert any(s.get("layer") == "team" for s in sk)   # skill UI hợp nhất (team) nạp được
    assert sk[-1]["layer"] == "core"                   # _core vẫn cuối, hiệu lực cao nhất

def test_ui_system_zone_owner_only():
    b = Blackboard("p")
    b.write("ui_system", UI, agent="b3_ui")
    assert b.get("ui_system")["screens"][0]["macrostructure"] == "split-hero"
    with pytest.raises(Exception):
        b.write("ui_system", UI, agent="c1_dev")    # sai writer → chặn

def test_ux_role_separate():
    sk = skill_loader.load("ux", None, {})     # UX vẫn tồn tại riêng
    assert sk[-1]["layer"] == "core"
