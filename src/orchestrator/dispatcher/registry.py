"""Sổ cấp module (có lock): depth guard + worker zombie.

- Depth tối đa 1: còn worker sống (kể cả thread con do worker mở) thì tạo/chạy Dispatcher → DepthExceeded.
- Worker bị KILL (quá ttl) thành zombie: owns của nó vẫn bị giữ với MỌI run() sau cùng dự án cho tới khi
  thread thật sự dừng.
"""
from __future__ import annotations

import threading


class DepthExceeded(Exception):
    pass


_REG = threading.Lock()
_ACTIVE = 0                        # worker đang chạy (chưa bị KILL) trong tiến trình
_WORKER_THREADS: set[int] = set()  # ident thread worker còn sống (kể cả zombie)
_ZOMBIES: dict[int, dict] = {}     # id(handle) -> {scope, task_id, owns, done: Event}


def check_depth(where: str) -> None:
    with _REG:
        if _ACTIVE > 0 or threading.get_ident() in _WORKER_THREADS:
            raise DepthExceeded(f"worker không được {where} Dispatcher (depth tối đa 1; "
                                f"{_ACTIVE} worker đang chạy)")


def live_zombies(scope=None) -> list[dict]:
    """Worker đã KILL nhưng thread chưa dừng (scope=None: mọi dự án)."""
    with _REG:
        return [dict(z) for z in _ZOMBIES.values() if scope is None or z["scope"] == scope]


def worker_started(scope: str) -> None:
    """Gọi trước khi start thread worker."""
    global _ACTIVE
    with _REG:
        _ACTIVE += 1


def thread_enter() -> int:
    """Gọi đầu thread worker: đánh dấu thread là worker."""
    ident = threading.get_ident()
    with _REG:
        _WORKER_THREADS.add(ident)
    return ident


def thread_exit(ident: int, scope: str, handle) -> None:
    """Gọi cuối thread worker: bỏ đánh dấu; zombie → báo đã dừng, không → giảm số đang chạy."""
    global _ACTIVE
    with _REG:
        _WORKER_THREADS.discard(ident)
        handle.finished = True
        if handle.zombie:
            z = _ZOMBIES.pop(id(handle), None)
            if z:
                z["done"].set()
        else:
            _ACTIVE -= 1


def zombify(handle, scope: str, owns: list[str]) -> bool:
    """Đánh dấu worker quá ttl là zombie (owns vẫn bị giữ). False nếu nó vừa kịp xong."""
    global _ACTIVE
    with _REG:
        if handle.finished:
            return False
        handle.zombie = True
        _ACTIVE -= 1
        _ZOMBIES[id(handle)] = {"scope": scope, "task_id": handle.task_id, "owns": list(owns),
                                "done": threading.Event()}
    return True
