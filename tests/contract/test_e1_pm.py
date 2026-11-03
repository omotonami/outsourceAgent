"""Contract test e1_pm — PM reporting chỉ đọc (Code thuần, không LLM). Output đúng schema, không ghi Blackboard."""
import json
from pathlib import Path

import pytest
from jsonschema import validate, ValidationError

from src.agents import e1_pm as agent
from src.blackboard.store import Blackboard, PermissionDenied

ID = "e1_pm"
SCHEMAS = Path(__file__).parents[2] / "specs/04_schemas"
OUT_SCHEMA = json.loads((SCHEMAS / f"{ID}.out.json").read_text(encoding="utf-8"))
IN_SCHEMA = json.loads((SCHEMAS / f"{ID}.in.json").read_text(encoding="utf-8"))
STATUS = {"phase": "BUILD", "budget_used_ratio": 0.85, "open_risks": 2, "overdue_questions": 1,
          "build_loops": 3, "tasks_done": 4, "tasks_total": 6, "gates_signed": ["G1", "G2"]}


def _board(status):
    b = Blackboard("p")
    b.write("project_status", status, agent="orchestrator")
    return b


def test_report_matches_output_schema_and_input_contract():
    validate({"project_status": STATUS}, IN_SCHEMA)
    out = agent.report(_board(STATUS))
    validate(out, OUT_SCHEMA)
    sev = {a["ref"]: a["severity"] for a in out["alerts"]}
    assert sev == {"budget": "warn", "risk_register": "warn", "requirements": "critical",
                   "build_results": "critical"}
    assert out["recommendations"] and "4/6" in out["summary"]


def test_report_is_read_only():
    b = _board(STATUS)
    before = json.dumps(b._data, sort_keys=True)
    agent.report(b)
    assert json.dumps(b._data, sort_keys=True) == before


def test_quiet_project_has_no_alerts():
    out = agent.report(_board({"phase": "DISCOVERY", "tasks_done": 0, "tasks_total": 1}))
    validate(out, OUT_SCHEMA)
    assert out["alerts"] == [] and out["recommendations"] == []


def test_pm_cannot_write_zones_or_sign():
    b = Blackboard("p")
    with pytest.raises(PermissionDenied):
        b.write("project_status", STATUS, agent=ID)
    with pytest.raises(PermissionDenied):
        b.sign("G1", by=ID, via="gate_ui")


def test_bad_severity_would_fail_schema():
    with pytest.raises(ValidationError):
        validate({"summary": "x", "alerts": [{"severity": "panic", "msg": "x"}]}, OUT_SCHEMA)
