"""Event log dùng chung (Code thuần) — type enum + MỘT hàm ghi.

- EventType: loại sự kiện team (Team feed hiển thị đúng các loại này, theo thứ tự này).
- emit(): sự kiện điều phối vào zone `trace_log` dạng {event, task_id, ts, by, ...} (Dispatcher).
- record(): lượt ghi log append-only khác (huddle_log, decision_records) qua cùng một chỗ, có kiểm
  zone được phép; nội dung giữ nguyên dạng hiện có (không thêm field).
- SOURCE_TYPES: zone nguồn → EventType mà Team feed quy về.
Quyền ghi thật vẫn do Blackboard enforce (specs/05_blackboard.yaml).
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Iterable


class EventType(str, Enum):
    ASSIGN = "ASSIGN"
    SPAWN = "SPAWN"
    RESULT = "RESULT"
    BLOCKED = "BLOCKED"
    DECISION = "DECISION"
    KILL = "KILL"


TYPES: tuple[str, ...] = tuple(t.value for t in EventType)
TRACE_ZONE = "trace_log"
SOURCE_TYPES = {"huddle_log": EventType.RESULT, "decision_records": EventType.DECISION,
                "risk_register": EventType.BLOCKED}


def emit(board, kind: EventType | str, *, agent: str, by: str, task_id: str | None = None,
         clock: Callable[[], float] = time.time, **fields) -> dict:
    """Append một sự kiện vào trace_log. `kind` ngoài EventType → ValueError."""
    entry = {"event": EventType(kind).value, "task_id": task_id, "ts": clock(), "by": by, **fields}
    board.append(TRACE_ZONE, entry, agent=agent)
    return entry


def record(board, zone: str, entry: dict, *, agent: str, allowed: Iterable[str]) -> None:
    """Append `entry` vào `zone` nếu zone thuộc `allowed` (phòng thủ thêm trước Blackboard)."""
    if zone not in allowed:
        raise PermissionError(f"{agent} không được ghi {zone}")
    board.append(zone, entry, agent=agent)
