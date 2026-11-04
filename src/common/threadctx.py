"""Thread con kế thừa một số contextvars của thread gọi `Thread.start()` (giống Python 3.14
`thread_inherit_context`, nhưng CHỈ khi có biến đã đăng ký đang được đặt).

- Module khác đăng ký ContextVar bằng `track(var)` (vd allocation budget, dòng dõi worker Dispatcher).
- `Thread.start()` từ thread mà KHÔNG có biến đăng ký nào được đặt → hành vi y như threading gốc.
- Có biến được đặt → thread con chạy `run()` trong bản sao context của thread cha (copy_context),
  nên thread con / thread pool mở từ worker thấy cùng giá trị. Không gắn cờ nào lên object Thread;
  ý nghĩa của giá trị (vd worker còn sống không) do module sở hữu kiểm ở thời điểm dùng.
Giới hạn: thread mở bằng `_thread.start_new_thread` trực tiếp không đi qua đây.
"""
from __future__ import annotations

import contextvars
import threading

_TRACKED: list[contextvars.ContextVar] = []
_MARK = "_threadctx_inherit_hook"


def track(var: contextvars.ContextVar) -> contextvars.ContextVar:
    """Đăng ký biến cần truyền sang thread con. Trả lại chính biến đó."""
    if var not in _TRACKED:
        _TRACKED.append(var)
    return var


def _active() -> bool:
    return any(v.get(None) is not None for v in _TRACKED)


def _install() -> None:
    orig = threading.Thread.start
    if getattr(orig, _MARK, False):
        return

    def start(self, *args, **kwargs):
        if _active():
            ctx = contextvars.copy_context()
            run = self.run

            def run_in_parent_ctx():
                return ctx.run(run)
            self.run = run_in_parent_ctx
        return orig(self, *args, **kwargs)

    setattr(start, _MARK, True)
    threading.Thread.start = start


_install()
