"""Sổ token theo (dự án, phase) — MỘT sổ duy nhất cho cả gateway và Dispatcher.

Thread-safe (lock). Mô hình reserve → commit:
- Trước khi gọi model: `reserve(...)` giữ chỗ một cận trên (max_tokens + cận trên token input).
  Nếu used + reserved + cần > trần → BudgetExceeded, KHÔNG gọi model.
- Sau khi gọi: `commit(r, actual)` trả phần giữ chỗ, cộng token thực tế. Lỗi → `cancel(r)`.
- Allocation (Dispatcher): giữ chỗ `worker_tokens` cho một worker; trong thread worker
  `use_allocation(a)` làm mọi lần gọi gateway rút từ allocation đó (vượt → BudgetExceeded),
  không giữ chỗ thêm trên sổ → không đếm hai lần. `release(a)` trả phần chưa dùng.
  Allocation nằm trong ContextVar: thread con start trong khối (threading.Thread, kể cả thread của pool được start trong khối) kế thừa
  qua src/common/threadctx.py → cũng rút từ allocation đó. Allocation đã release → về sổ chung.
Trần = per_project_tokens[phase] * block_auto_escalate_at_ratio (config/budgets.yaml); null = không trần.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
from collections import defaultdict
from dataclasses import dataclass
from src.common import config, threadctx

_CFG = config.load_yaml(config.BUDGETS)
_LOCK = threading.RLock()
_USED: dict[tuple[str, str], int] = defaultdict(int)
_RESERVED: dict[tuple[str, str], int] = defaultdict(int)
_ALLOC: contextvars.ContextVar["Reservation | None"] = threadctx.track(
    contextvars.ContextVar("budget_allocation", default=None))


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class Reservation:
    project_id: str
    phase: str
    amount: int
    parent: "Reservation | None" = None
    consumed: int = 0       # chỉ dùng cho allocation
    held: int = 0           # allocation: phần đang giữ cho các lần gọi chưa commit
    closed: bool = False

    @property
    def key(self):
        return (self.project_id, self.phase)


def limit(project_id: str, phase: str) -> float | None:
    cap = (_CFG.get("per_project_tokens") or {}).get(phase)
    return None if cap is None else cap * _CFG["block_auto_escalate_at_ratio"]


def used(project_id: str, phase: str) -> int:
    with _LOCK:
        return _USED[(project_id, phase)]


def reserved(project_id: str, phase: str) -> int:
    with _LOCK:
        return _RESERVED[(project_id, phase)]


def remaining(project_id: str, phase: str) -> float | None:
    lim = limit(project_id, phase)
    if lim is None:
        return None
    with _LOCK:
        k = (project_id, phase)
        return lim - _USED[k] - _RESERVED[k]


def check(project_id: str, phase: str) -> None:
    """Tương thích cũ: báo lỗi nếu đã chạm trần (không giữ chỗ)."""
    lim = limit(project_id, phase)
    with _LOCK:
        k = (project_id, phase)
        if lim is not None and _USED[k] + _RESERVED[k] >= lim:
            raise BudgetExceeded(f"{project_id}/{phase} vượt budget, cần người duyệt")


def reserve(project_id: str, phase: str, tokens: int) -> Reservation:
    """Giữ chỗ trên sổ (không dùng allocation). Vượt trần → BudgetExceeded."""
    tokens = max(0, int(tokens))
    lim = limit(project_id, phase)
    with _LOCK:
        k = (project_id, phase)
        if lim is not None and _USED[k] + _RESERVED[k] + tokens > lim:
            raise BudgetExceeded(f"{project_id}/{phase} vượt budget, cần người duyệt "
                                 f"(đã dùng {_USED[k]}, giữ chỗ {_RESERVED[k]}, cần {tokens}, trần {lim:g})")
        _RESERVED[k] += tokens
        return Reservation(project_id, phase, tokens)


def reserve_call(project_id: str, phase: str, tokens: int) -> Reservation:
    """Giữ chỗ cho MỘT lần gọi model: rút từ allocation của context hiện tại nếu có (cùng dự án/phase)."""
    alloc = _ALLOC.get()
    if alloc is None or alloc.closed or alloc.key != (project_id, phase):
        return reserve(project_id, phase, tokens)
    tokens = max(0, int(tokens))
    with _LOCK:
        free = alloc.amount - alloc.consumed - alloc.held
        if tokens > free:
            raise BudgetExceeded(f"{project_id}/{phase} vượt budget worker "
                                 f"(còn {free}, cần {tokens}, allocation {alloc.amount})")
        alloc.held += tokens
        return Reservation(project_id, phase, tokens, parent=alloc)


def _alloc_reserved(a: Reservation) -> int:
    return max(0, a.amount - a.consumed)


def commit(r: Reservation, actual: int) -> None:
    """Chốt token thực tế cho một lần gọi đã giữ chỗ."""
    actual = max(0, int(actual))
    with _LOCK:
        if r.closed:
            return
        r.closed = True
        _USED[r.key] += actual
        if r.parent is None:
            _RESERVED[r.key] -= r.amount
            return
        a = r.parent
        before = _alloc_reserved(a)
        a.held -= r.amount
        a.consumed += actual
        if not a.closed:
            _RESERVED[a.key] -= before - _alloc_reserved(a)


def cancel(r: Reservation) -> None:
    """Huỷ giữ chỗ của lần gọi lỗi (không tính token)."""
    with _LOCK:
        if r.closed:
            return
        r.closed = True
        if r.parent is None:
            _RESERVED[r.key] -= r.amount
        else:
            r.parent.held -= r.amount


def release(a: Reservation | None) -> None:
    """Trả phần allocation chưa dùng về sổ."""
    if a is None:
        return
    with _LOCK:
        if a.closed:
            return
        a.closed = True
        _RESERVED[a.key] -= _alloc_reserved(a)


@contextlib.contextmanager
def use_allocation(a: Reservation | None):
    """Trong khối này, mọi lần gọi gateway của thread hiện tại (và thread con mở trong khối)
    rút từ allocation `a`."""
    tok = _ALLOC.set(a)
    try:
        yield a
    finally:
        _ALLOC.reset(tok)


def add(project_id: str, phase: str, tokens: int) -> None:
    """Tương thích cũ: cộng thẳng token đã dùng."""
    with _LOCK:
        _USED[(project_id, phase)] += max(0, int(tokens))


def reset(project_id: str | None = None) -> None:
    """Xoá sổ (test / đóng dự án)."""
    with _LOCK:
        for d in (_USED, _RESERVED):
            for k in [k for k in d if project_id is None or k[0] == project_id]:
                del d[k]
