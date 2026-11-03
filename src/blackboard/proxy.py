"""Board proxy — MỘT chỗ định nghĩa quyền thu hẹp của Blackboard cho bên không được ghi tự do.

- ReadOnlyBoard: chỉ get / is_signed; write / append / sign / revoke_signature → PermissionDenied.
  Dùng cho e2_cos (Chief of Staff) và Team feed.
- WorkerBoard: đọc + write/append CHỈ với danh tính agent con (specs/05_blackboard.yaml vẫn quyết zone);
  KHÔNG có sign / revoke_signature (AttributeError). Dùng cho worker của Dispatcher.
"""
from __future__ import annotations

from typing import Iterable

from src.blackboard.store import PermissionDenied


class ReadOnlyBoard:
    """Board chỉ đọc. `owner`: tên bên dùng proxy, hiện trong thông báo lỗi."""
    __slots__ = ("_ReadOnlyBoard__b", "_owner", "project_id")

    def __init__(self, board, owner: str = "proxy"):
        self.__b, self._owner = board, owner
        self.project_id = getattr(board, "project_id", None)

    def get(self, zone):
        return self.__b.get(zone)

    def is_signed(self, gate):
        return self.__b.is_signed(gate)

    def _deny(self, what: str):
        raise PermissionDenied(f"{self._owner} chỉ đọc: không được {what} (gate chỉ qua gate_ui do người ký)")

    def write(self, zone, value, agent=None):
        self._deny(f"ghi {zone}")

    def append(self, zone, entry, agent=None):
        self._deny(f"append {zone}")

    def sign(self, gate, by=None, via=None):
        self._deny(f"ký gate {gate}")

    def revoke_signature(self, gate, reason=None, by=None):
        self._deny(f"thu hồi gate {gate}")


class WorkerBoard:
    """Board proxy cho worker: đọc được; ghi/append chỉ với danh tính agent con.
    KHÔNG có sign / revoke_signature / ghi task_backlog / trace_log."""
    __slots__ = ("_WorkerBoard__b", "_WorkerBoard__agents", "project_id")

    def __init__(self, board, agents: Iterable[str]):
        self.__b, self.__agents = board, frozenset(agents)
        self.project_id = getattr(board, "project_id", None)

    def get(self, zone):
        return self.__b.get(zone)

    def is_signed(self, gate):
        return self.__b.is_signed(gate)

    def _who(self, agent):
        if agent not in self.__agents:
            raise PermissionDenied(f"worker chỉ ghi với danh tính {sorted(self.__agents)}, không phải {agent!r}")

    def write(self, zone, value, agent):
        self._who(agent)
        return self.__b.write(zone, value, agent=agent)

    def append(self, zone, entry, agent):
        self._who(agent)
        return self.__b.append(zone, entry, agent=agent)
