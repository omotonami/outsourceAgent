"""Board proxy — MỘT chỗ định nghĩa quyền thu hẹp của Blackboard cho bên không được ghi tự do.

- ReadOnlyBoard: chỉ get / is_signed; write / append / sign / revoke_signature → PermissionDenied.
  Dùng cho e2_cos (Chief of Staff) và Team feed.
- WorkerBoard: đọc + write/append CHỈ với danh tính agent con (specs/05_blackboard.yaml vẫn quyết zone);
  KHÔNG có sign / revoke_signature (AttributeError). Dùng cho worker của Dispatcher.
  `cancelled` (callable → bool): kiểm trước MỖI lần ghi (trong `lock` nếu có, cùng lock với KILL) —
  worker đã bị KILL (zombie) ghi → PermissionDenied.
Board thật KHÔNG nằm ở thuộc tính nào của proxy (kể cả tên bị mangle): các thao tác là closure
tạo trong __init__, lưu ở __slots__; không thêm được thuộc tính mới (vd `_data`).
Đây là rào chống dùng nhầm, không phải sandbox: code Python cố ý vẫn introspect được closure.
"""
from __future__ import annotations

import contextlib
from typing import Callable, Iterable

from src.blackboard.store import PermissionDenied


def _readers(board) -> tuple[Callable, Callable]:
    def get(zone):
        return board.get(zone)

    def is_signed(gate):
        return board.is_signed(gate)
    return get, is_signed


class ReadOnlyBoard:
    """Board chỉ đọc. `owner`: tên bên dùng proxy, hiện trong thông báo lỗi."""
    __slots__ = ("get", "is_signed", "project_id", "_owner")

    def __init__(self, board, owner: str = "proxy"):
        self.get, self.is_signed = _readers(board)
        self.project_id = getattr(board, "project_id", None)
        self._owner = owner

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
    __slots__ = ("get", "is_signed", "write", "append", "project_id")

    def __init__(self, board, agents: Iterable[str], cancelled: Callable[[], bool] | None = None,
                 lock=None):
        allowed = frozenset(agents)
        is_cancelled = cancelled or (lambda: False)
        guard = (lambda: lock) if lock is not None else contextlib.nullcontext

        def _who(agent):
            if agent not in allowed:
                raise PermissionDenied(f"worker chỉ ghi với danh tính {sorted(allowed)}, không phải {agent!r}")

        def _alive(zone):
            if is_cancelled():
                raise PermissionDenied(f"worker đã bị KILL (zombie): không được ghi {zone}")

        def write(zone, value, agent):
            _who(agent)
            with guard():
                _alive(zone)
                return board.write(zone, value, agent=agent)

        def append(zone, entry, agent):
            _who(agent)
            with guard():
                _alive(zone)
                return board.append(zone, entry, agent=agent)

        self.get, self.is_signed = _readers(board)
        self.write, self.append = write, append
        self.project_id = getattr(board, "project_id", None)
