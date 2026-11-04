"""src/orchestrator/events: type enum + emit dùng chung (Dispatcher, Team feed, Huddle)."""
import pytest

from src.blackboard.store import Blackboard, PermissionDenied
from src.orchestrator import events, team_feed


def test_types_order_matches_team_feed():
    assert events.TYPES == ("ASSIGN", "SPAWN", "RESULT", "BLOCKED", "DECISION", "KILL")
    assert team_feed.TYPES is events.TYPES


def test_emit_shape_and_writer():
    b = Blackboard("p-ev")
    e = events.emit(b, events.EventType.ASSIGN, writer="orchestrator", by="orchestrator.dispatcher",
                    task_id="T1", clock=lambda: 1.0, agent="c1_dev")
    assert e == {"event": "ASSIGN", "task_id": "T1", "ts": 1.0, "by": "orchestrator.dispatcher",
                 "agent": "c1_dev"}
    assert b.get("trace_log")[0]["source_agent"] == "orchestrator"
    with pytest.raises(ValueError):
        events.emit(b, "NOPE", writer="orchestrator", by="x")
    with pytest.raises(PermissionDenied):                      # Blackboard vẫn quyết writer
        events.emit(b, "SPAWN", writer="c1_dev", by="x")


def test_record_only_allowed_zones():
    b = Blackboard("p-ev")
    events.record(b, "huddle_log", {"round": 1}, agent="huddle", allowed=("huddle_log",))
    assert b.get("huddle_log") == [{"round": 1, "source_agent": "huddle"}]
    with pytest.raises(PermissionError):
        events.record(b, "requirements", {}, agent="huddle", allowed=("huddle_log",))


@pytest.mark.parametrize("status,open_", [(None, True), ("open", True), ("Closed", False), ("resolved", False)])
def test_is_open_risk(status, open_):
    assert team_feed.is_open_risk({"status": status}) is open_
