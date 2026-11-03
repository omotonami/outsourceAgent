"""Sổ cấp module (có lock): depth guard + worker zombie.

Depth tối đa 1, chặn THEO DỰ ÁN (scope = project_id):
- Thread worker, và mọi thread do worker (hoặc thread con của nó) mở, KHÔNG được tạo/chạy Dispatcher
  của BẤT KỲ dự án nào → DepthExceeded. Dòng dõi thread được đánh dấu khi `Thread.start()` gọi từ thread
  thuộc dòng worker (hook cài một lần, chỉ gắn cờ, không đổi hành vi khác của threading).
- Thread không thuộc dòng worker: tạo/chạy Dispatcher của dự án X bị chặn khi X còn worker đang chạy;
  dự án khác chạy bình thường.
- Worker bị KILL (quá ttl) thành zombie: không còn tính là "đang chạy", nhưng owns của nó vẫn bị giữ với
  MỌI run() sau cùng dự án cho tới khi thread thật sự dừng.
Giới hạn: thread mở bằng `_thread.start_new_thread` trực tiếp (bỏ qua threading.Thread) không được
đánh dấu dòng dõi — vẫn bị chặn với dự án của chính worker đó, nhưng không với dự án khác.
"""
from __future__ import annotations

import threading
from collections import defaultdict


class DepthExceeded(Exception):
    pass


_REG = threading.Lock()
_ACTIVE: dict[str, int] = defaultdict(int)   # scope → worker đang chạy (chưa bị KILL)
_WORKER_THREADS: set[int] = set()            # ident thread worker còn sống (kể cả zombie)
_ZOMBIES: dict[int, dict] = {}               # id(handle) -> {scope, task_id, owns, done: Event}
_LINEAGE_ATTR = "_dispatcher_worker_lineage"


def in_worker_lineage() -> bool:
    """Thread hiện tại là worker, hoặc được mở (trực tiếp/gián tiếp) từ một worker."""
    return (threading.get_ident() in _WORKER_THREADS
            or getattr(threading.current_thread(), _LINEAGE_ATTR, False))


def _install_lineage_hook() -> None:
    orig = threading.Thread.start
    if getattr(orig, "_dispatcher_lineage_hook", False):
        return

    def start(self, *args, **kwargs):
        if in_worker_lineage():
            setattr(self, _LINEAGE_ATTR, True)
        return orig(self, *args, **kwargs)

    start._dispatcher_lineage_hook = True
    start.__wrapped__ = orig
    threading.Thread.start = start


_install_lineage_hook()


def check_depth(where: str, scope: str | None) -> None:
    """DepthExceeded nếu gọi từ dòng worker, hoặc dự án `scope` còn worker đang chạy."""
    with _REG:
        if in_worker_lineage():
            raise DepthExceeded(f"worker không được {where} Dispatcher (depth tối đa 1)")
        n = _ACTIVE.get(scope, 0) if scope is not None else 0
        if n > 0:
            raise DepthExceeded(f"không được {where} Dispatcher cho dự án {scope!r}: "
                                f"{n} worker của dự án đang chạy (depth tối đa 1)")


def active(scope: str) -> int:
    """Số worker đang chạy (chưa KILL) của dự án."""
    with _REG:
        return _ACTIVE.get(scope, 0)


def live_zombies(scope=None) -> list[dict]:
    """Worker đã KILL nhưng thread chưa dừng (scope=None: mọi dự án)."""
    with _REG:
        return [dict(z) for z in _ZOMBIES.values() if scope is None or z["scope"] == scope]


def worker_started(scope: str) -> None:
    """Gọi trước khi start thread worker."""
    with _REG:
        _ACTIVE[scope] += 1


def thread_enter() -> int:
    """Gọi đầu thread worker: đánh dấu thread là worker."""
    ident = threading.get_ident()
    with _REG:
        _WORKER_THREADS.add(ident)
    return ident


def _done(scope: str) -> None:
    _ACTIVE[scope] -= 1
    if _ACTIVE[scope] <= 0:
        del _ACTIVE[scope]


def thread_exit(ident: int, scope: str, handle) -> None:
    """Gọi cuối thread worker: bỏ đánh dấu; zombie → báo đã dừng, không → giảm số đang chạy."""
    with _REG:
        _WORKER_THREADS.discard(ident)
        handle.finished = True
        if handle.zombie:
            z = _ZOMBIES.pop(id(handle), None)
            if z:
                z["done"].set()
        else:
            _done(scope)


def zombify(handle, scope: str, owns: list[str]) -> bool:
    """Đánh dấu worker quá ttl là zombie (owns vẫn bị giữ). False nếu nó vừa kịp xong."""
    with _REG:
        if handle.finished:
            return False
        handle.zombie = True
        _done(scope)
        _ZOMBIES[id(handle)] = {"scope": scope, "task_id": handle.task_id, "owns": list(owns),
                                "done": threading.Event()}
    return True
