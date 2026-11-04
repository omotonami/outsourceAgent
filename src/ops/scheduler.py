"""Scheduler đa dự án — Code thuần, KHÔNG LLM quyết (đợt Control Room 2026-11-04).

  python -m src.ops.scheduler run [--once] [--mock] [--db path]
  python -m src.ops.scheduler status [--db path]

Mỗi vòng: duyệt mọi project trong event store →
  - project đang mở (không RELEASED/ABANDONED/ESCALATED, không chờ gate chưa ký, không BLOCKED mà store chưa đổi)
    → chạy tiếp bằng pipeline (src/ops/pipeline.py) tới gate / ESCALATED / BLOCKED, mỗi project một thread;
  - task giao thêm (zone side_tasks, src/ops/side_tasks.py) đang queued → chạy khi role được phép.
Mô hình nhân sự (config/scheduler.yaml): mỗi role (agent_id) tối đa `max_main_per_role` việc chính cùng lúc
(bước pipeline / task kind=main) + tối đa `max_side_per_role` việc phụ; việc phụ CHỈ chạy khi role rảnh: không
có việc chính đang chạy / đang chờ role, và không dự án đang chạy nào ở phase cần role đó (STATE_ROLES). `max_concurrent_calls` = tổng lượt chạy agent song song.
ASSIGN trong trace_log mang `project` + `kind` (main|side).

KHÔNG có đường ký gate / gỡ ESCALATED / chạy huddle ở đây (luật 10; huddle vẫn gọi tay `project huddle`).
Khoá file (cạnh event store) chống 2 process scheduler cùng lúc → process thứ 2 thoát mã 3.
Exit code: 0 xong (--once) / dừng bằng Ctrl-C · 1 lỗi đầu vào · 3 scheduler khác đang chạy.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextlib
import os
import signal
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

from src.blackboard.store import Blackboard
from src.common import config
from src.orchestrator import gate_view

from . import common, pipeline, side_tasks

CFG = "config/scheduler.yaml"
KEYS = ("max_main_per_role", "max_side_per_role", "max_concurrent_calls", "max_projects", "poll_s", "lock_file")
CLOSED = ("RELEASED", "ABANDONED", "ESCALATED")
_G = gate_view.GATE_AGENTS
# Role mà dự án ở state này sắp cần (việc chính) → việc phụ của role đó chờ (ưu tiên việc chính).
STATE_ROLES = {"INTAKE": _G["G1"], "PRESALES": _G["G1"], "AWAIT_G1": _G["G2"], "DISCOVERY": _G["G2"],
               "AWAIT_G2": _G["G3"], "BUILD": _G["G3"], "AWAIT_G3": _G["G4"], "SECURITY": _G["G4"]}


def settings(root=None) -> dict:
    sec = config.section(config.load_yaml(CFG, root), "scheduler", CFG)
    out = {k: config.require(sec, k, CFG) for k in KEYS}
    for k in KEYS[:4]:
        if not isinstance(out[k], int) or isinstance(out[k], bool) or out[k] < (0 if k == "max_side_per_role" else 1):
            raise config.ConfigError(f"{CFG}: scheduler.{k} phải là số nguyên hợp lệ")
    return out


# ---------- khoá chống 2 scheduler ----------
class LockBusy(RuntimeError):
    pass


class FileLock:
    """flock(LOCK_EX|LOCK_NB) trên file cạnh event store. Nhả khi đóng / process chết (OS tự nhả)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:                          # Windows: không có fcntl → msvcrt
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as e:
                os.close(fd)
                raise LockBusy(f"scheduler khác đang chạy (khoá {self.path})") from e
        except OSError as e:
            os.close(fd)
            raise LockBusy(f"scheduler khác đang chạy (khoá {self.path})") from e
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *a):
        self.release()


def lock_path(store, cfg: dict) -> Path:
    return Path(store.path).parent / cfg["lock_file"]


# ---------- slot nhân sự ----------
class Slots:
    """Trần việc chính / việc phụ theo role + tổng lượt chạy song song. Thread-safe (Condition).
    `peak` ghi mức cao nhất quan sát được (role, kind) → n — để test/giám sát."""

    def __init__(self, max_main: int, max_side: int, max_calls: int):
        self.max_main, self.max_side, self.max_calls = max_main, max_side, max_calls
        self.cv = threading.Condition()
        self.main: dict[str, list[str]] = defaultdict(list)
        self.side: dict[str, list[str]] = defaultdict(list)
        self.waiting: dict[str, int] = defaultdict(int)
        self.calls = 0
        self.peak: dict[tuple[str, str], int] = defaultdict(int)
        self.peak_calls = 0

    def _free(self, roles, kind) -> bool:
        if self.calls >= self.max_calls:
            return False
        if kind == "main":
            return all(len(self.main[r]) < self.max_main for r in roles)
        return all(not self.main[r] and not self.waiting[r] and len(self.side[r]) < self.max_side for r in roles)

    def _take(self, roles, pid, kind) -> None:
        tab = self.main if kind == "main" else self.side
        for r in roles:
            tab[r].append(pid)
            self.peak[(r, kind)] = max(self.peak[(r, kind)], len(tab[r]))
        self.calls += 1
        self.peak_calls = max(self.peak_calls, self.calls)

    def acquire(self, roles, pid: str, kind: str = "main", block: bool = True) -> bool:
        roles = tuple(roles)
        with self.cv:
            if kind == "main" and block:
                for r in roles:
                    self.waiting[r] += 1
                try:
                    while not self._free(roles, kind):
                        self.cv.wait(timeout=1.0)
                finally:
                    for r in roles:
                        self.waiting[r] -= 1
            elif not self._free(roles, kind):
                return False
            self._take(roles, pid, kind)
            return True

    def release(self, roles, pid: str, kind: str = "main") -> None:
        tab = self.main if kind == "main" else self.side
        with self.cv:
            for r in roles:
                if pid in tab[r]:
                    tab[r].remove(pid)
            self.calls -= 1
            self.cv.notify_all()

    @contextlib.contextmanager
    def hold(self, roles, pid: str, kind: str = "main"):
        roles = tuple(roles)
        self.acquire(roles, pid, kind, block=True)
        try:
            yield
        finally:
            self.release(roles, pid, kind)

    def busy(self) -> dict:
        with self.cv:
            return {"main": {r: list(v) for r, v in self.main.items() if v},
                    "side": {r: list(v) for r, v in self.side.items() if v}, "calls": self.calls}


# ---------- scheduler ----------
class Scheduler:
    def __init__(self, store, sender=None, cfg: dict | None = None, log=print):
        self.store, self.sender, self.cfg = store, sender, cfg or settings()
        self.slots = Slots(self.cfg["max_main_per_role"], self.cfg["max_side_per_role"],
                           self.cfg["max_concurrent_calls"])
        self.pool = cf.ThreadPoolExecutor(max_workers=self.cfg["max_projects"] + self.cfg["max_concurrent_calls"],
                                          thread_name_prefix="sched")
        self.running: dict[tuple, cf.Future] = {}
        self.skip_seq: dict[str, int] = {}          # BLOCKED: bỏ qua tới khi store của project đổi
        self.outcomes: dict[str, pipeline.Outcome] = {}
        self.tasks_done: list[dict] = []
        self.log = log
        self._first = True                           # vòng đầu: task 'started' cũ (process trước chết) chạy lại
        self._mu = threading.Lock()

    # --- chạy ---
    def _run_project(self, pid: str) -> pipeline.Outcome:
        o = pipeline.run(self.store, pid, sender=self.sender, slots=self.slots)
        with self._mu:
            self.outcomes[pid] = o
            if o.stop == "BLOCKED":
                self.skip_seq[pid] = self.store.last_seq(pid)
        self.log(f"[{pid}] dừng: {o.stop}" + (f" ({o.gate})" if o.gate else "") +
                 (f" · {o.reason}" if o.reason else "") + (f" · đã chạy: {', '.join(o.ran)}" if o.ran else ""))
        return o

    def _run_task(self, pid: str, task: dict) -> dict:
        roles, kind = (task["role"],), task.get("kind") or "side"
        try:
            b = Blackboard.resume(pid, self.store)
            end = side_tasks.run_one(b, task, pid, sender=self.sender)
        finally:
            self.slots.release(roles, pid, kind)
        with self._mu:
            self.tasks_done.append({"project": pid, **end})
        self.log(f"[{pid}] việc {kind} {task['id']} ({task['role']}): {end['event']}")
        return end

    def _reap(self) -> None:
        for k, f in list(self.running.items()):
            if f.done():
                del self.running[k]
                if (e := f.exception()) is not None:
                    self.log(f"LỖI {k}: {type(e).__name__}: {e}")

    def scan(self) -> int:
        """Một vòng: nộp project / task chạy được. Trả số việc mới nộp."""
        self._reap()
        n = 0
        projects = sorted(self.store.projects(), key=lambda p: p["id"])
        n_proj = sum(1 for k in self.running if k[0] == "project")
        queued: list[tuple[str, dict]] = []
        wanted: set[str] = set()                      # role dự án đang chạy / sắp chạy cần
        for p in projects:
            pid = p["id"]
            b = Blackboard.load(pid, self.store)
            st = common.state_of(b)
            if st != "ABANDONED":
                stale = ("started",) if self._first else ()
                queued += [(pid, t) for t in side_tasks.tasks(b) if t["status"] in ("queued", *stale)]
            if ("project", pid) in self.running:
                wanted |= set(STATE_ROLES.get(st, ()))
                continue
            if st in CLOSED or b.get("lead_brief") is None:
                continue
            gate = common.pending_gate(st)
            if gate and not b.is_signed(gate):
                continue
            if self.skip_seq.get(pid) == p["last_seq"]:
                continue
            if n_proj >= self.cfg["max_projects"]:
                continue
            self.running[("project", pid)] = self.pool.submit(self._run_project, pid)
            wanted |= set(STATE_ROLES.get(st, ()))
            n_proj += 1
            n += 1
        self._first = False
        want_main = {t["role"] for _, t in queued if t.get("kind") == "main"}
        for pid, t in queued:                         # việc chính trước, rồi việc phụ (role rảnh mới chạy)
            if t.get("kind") != "main":
                continue
            n += self._try_task(pid, t)
        for pid, t in queued:
            if t.get("kind") == "main" or t["role"] in want_main or t["role"] in wanted:
                continue
            n += self._try_task(pid, t)
        return n

    def _try_task(self, pid: str, t: dict) -> int:
        key = ("task", pid, t["id"])
        if key in self.running:
            return 0
        kind = t.get("kind") or "side"
        if not self.slots.acquire((t["role"],), pid, kind, block=False):
            return 0
        self.running[key] = self.pool.submit(self._run_task, pid, t)
        return 1

    def run_once(self, timeout: float | None = None) -> dict:
        """Chạy tới khi không còn gì chạy được: mọi project ở điểm dừng, task queued không chạy được nữa."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self.scan()
            if not self.running:
                break
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if left == 0.0:
                break
            cf.wait(list(self.running.values()), timeout=left, return_when=cf.FIRST_COMPLETED)
        return self.summary()

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.scan()
            futs = list(self.running.values())
            if futs:
                cf.wait(futs, timeout=self.cfg["poll_s"], return_when=cf.FIRST_COMPLETED)
            else:
                stop.wait(self.cfg["poll_s"])
        cf.wait(list(self.running.values()))

    def summary(self) -> dict:
        return {"outcomes": {p: {"stop": o.stop, "state": o.state, "gate": o.gate, "reason": o.reason}
                             for p, o in sorted(self.outcomes.items())},
                "tasks": list(self.tasks_done), "running": [list(k) for k in self.running],
                "peak": {f"{r}:{k}": v for (r, k), v in sorted(self.slots.peak.items())},
                "peak_calls": self.slots.peak_calls}

    def close(self) -> None:
        self.pool.shutdown(wait=True)


# ---------- CLI ----------
def _sender(mock_flag: bool):
    if mock_flag:
        from . import mock
        return mock.sender
    return None


def cmd_run(a) -> int:
    store = common.open_store(a.db)
    cfg = settings()
    lk = FileLock(lock_path(store, cfg))
    try:
        lk.acquire()
    except LockBusy as e:
        print(f"TỪ CHỐI: {e}", file=sys.stderr)
        return 3
    sch = Scheduler(store, sender=_sender(a.mock), cfg=cfg)
    try:
        print(f"Scheduler · event store {store.path} · trần: main/role={cfg['max_main_per_role']} "
              f"side/role={cfg['max_side_per_role']} song song={cfg['max_concurrent_calls']}"
              + (" · MOCK" if a.mock else ""))
        if a.once:
            s = sch.run_once()
            print("Xong một lượt:")
            for pid, o in s["outcomes"].items():
                print(f"  {pid:<28} {o['stop']:<10} {o['gate'] or ''}")
            print("CẦN NGƯỜI LÀM: ký gate / gỡ ESCALATED trên Control Room (http://localhost:8000/) — Claude không làm thay.")
            return 0
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        sch.run_forever(stop)
        return 0
    finally:
        sch.close()
        lk.release()


def cmd_status(a) -> int:
    store = common.open_store(a.db, readonly=True)
    cfg = settings()
    lp = lock_path(store, cfg)
    try:
        with FileLock(lp):
            running = False
    except LockBusy:
        running = True
    print(f"scheduler: {'ĐANG CHẠY' if running else 'không chạy'} (khoá {lp})")
    for p in sorted(store.projects(), key=lambda x: x["id"]):
        b = Blackboard.load(p["id"], store)
        q = [t for t in side_tasks.tasks(b) if t["status"] in side_tasks.OPEN]
        print(f"  {p['id']:<28} {common.state_of(b):<10} task chờ/chạy: {len(q)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.scheduler", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="chạy nền (Ctrl-C để dừng) hoặc --once một lượt")
    p.add_argument("--once", action="store_true")
    p.add_argument("--mock", action="store_true", help="sender mock, không cần API key")
    p.add_argument("--db", default=None)
    p = sub.add_parser("status")
    p.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
