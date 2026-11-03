"""PM reporting (chỉ đọc) + project_status do Orchestrator ghi."""
from src.blackboard.store import Blackboard, PermissionDenied
from src.orchestrator import status as st
from src.agents import e1_pm
import pytest

def _board_with_state():
    b = Blackboard("p")
    b.write("task_backlog", [{"id":"T1","status":"merged"},{"id":"T2","status":"ready"}], agent="b4_tech_lead")
    b.append("risk_register", {"risk":"x","severity":"High","status":"open"}, agent="a2_estimation")
    b.write("requirements", {"stories":[], "open_questions":[{"q":"FX?","owner":"PM","due":"2000-01-01"}]}, agent="b1_ba")
    return b

def test_orchestrator_writes_project_status():
    b=_board_with_state()
    ps = st.update(b, "BUILD", budget_ratio=0.85)
    assert ps["phase"]=="BUILD" and ps["tasks_total"]==2 and ps["tasks_done"]==1
    assert ps["open_risks"]==1 and ps["overdue_questions"]==1

def test_pm_is_read_only():
    b=_board_with_state()
    # PM agent KHÔNG có quyền ghi bất kỳ vùng nào
    with pytest.raises(PermissionDenied):
        b.write("project_status", {}, agent="e1_pm")
    with pytest.raises(PermissionDenied):
        b.write("business_context", {}, agent="e1_pm")

def test_pm_report_raises_alerts_not_decisions():
    b=_board_with_state()
    st.update(b, "BUILD", budget_ratio=0.9)
    rep = e1_pm.report(b)
    sev = {a["severity"] for a in rep["alerts"]}
    assert "critical" in sev and "warn" in sev          # có cảnh báo
    assert "summary" in rep and "Phase BUILD" in rep["summary"]
    # PM chỉ khuyến nghị, không quyết
    assert all("nên" in r or "người" in r for r in rep["recommendations"])
