"""Vòng điều phối của Dispatcher (mixin): chọn task ready, spawn worker, chờ kết quả, KILL quá ttl.

Thao tác ghi (_set_status, _event, _block, _spawn, _on_result...) nằm ở core.Dispatcher.
"""
from __future__ import annotations

import concurrent.futures as cf
import time

from src.blackboard.proxy import WorkerBoard
from src.gateway import budget as ledger

from . import gitops, registry
from .dag import _overlap, check_dag
from .settings import BLOCKED, DEFAULT_AGENT, DEFAULT_TASK_TYPE, DONE, WORKER_STATES
from .worker import WorkerHandle

# running: future → (task_id, owns, deadline monotonic, handle)
Running = dict[cf.Future, tuple[str, list[str], float, WorkerHandle]]


class Scheduler:
    def run(self) -> dict:
        registry.check_depth("chạy", getattr(self, "scope", None))
        tasks = self._backlog()
        check_dag(tasks)
        # Trạng thái dở từ lần chạy trước (không phải xong/blocked) → làm lại từ đầu.
        for t in tasks:
            if t.get("status") in WORKER_STATES:
                self._set_status(t["id"], "ready")

        running: Running = {}
        spawned = 0
        while True:
            tb = self._cascade_blocked()
            zombies = registry.live_zombies(self.scope)          # gồm cả zombie của run() trước
            busy = {tid for tid, *_ in running.values()} | {z["task_id"] for z in zombies}
            ready = self._mark_ready(tb, busy)
            held = [o for _, o, *_ in running.values()] + [z["owns"] for z in zombies]
            waiting, n = self._spawn_ready(ready, running, zombies, held, busy)
            spawned += n

            if not running:
                if zombies and waiting and not self.escalation:
                    if self._wait_zombies(zombies):
                        continue
                    self.escalation = "worker bị KILL chưa dừng, owns còn bị giữ"
                    self._event("BLOCKED", None, reason=self.escalation, pending=waiting,
                                zombies=[z["task_id"] for z in zombies])
                break
            self._reap(running)

        return self._summary(spawned)

    def _cascade_blocked(self) -> dict[str, dict]:
        """Dep blocked → task phụ thuộc (chưa chạy) cũng blocked. Trả backlog mới theo id."""
        tb = {t["id"]: t for t in self._backlog()}
        for t in tb.values():
            if t.get("status") in DONE or t.get("status") == BLOCKED:
                continue
            bad = [d for d in t.get("deps") or [] if tb[d].get("status") == BLOCKED]
            if bad and t.get("status") in (None, "ready"):
                self._block(t["id"], f"dep blocked: {bad}")
        return {t["id"]: t for t in self._backlog()}

    def _mark_ready(self, tb: dict[str, dict], busy: set[str]) -> list[dict]:
        """Task chưa chạy, không bận, mọi dep đã xong → ready (ghi status nếu chưa có)."""
        ready = [t for t in tb.values()
                 if t.get("status") in (None, "ready") and t["id"] not in busy
                 and all(tb[d].get("status") in DONE for d in t.get("deps") or [])]
        for t in ready:
            if t.get("status") is None:
                self._set_status(t["id"], "ready")
        return ready

    def _spawn_ready(self, ready: list[dict], running: Running, zombies: list[dict],
                     held: list[list[str]], busy: set[str]) -> tuple[list[str], int]:
        """Spawn các task ready được phép. Trả (task chờ vì owns/chỗ, số worker đã spawn)."""
        waiting: list[str] = []
        spawned = 0
        for t in ready:
            if self.escalation:
                break
            if len(running) + len(zombies) >= self.max_parallel:
                waiting.append(t["id"])
                break
            if t["id"] in self.autonomy_denied:
                continue
            agent, task_type = t.get("agent") or DEFAULT_AGENT, t.get("type") or DEFAULT_TASK_TYPE
            gate = self.autonomy.can_run(agent, task_type, self.trigger)
            if not gate["allowed"]:  # giữ ready, chỉ báo BLOCKED một lần mỗi run
                self.autonomy_denied[t["id"]] = gate["reason"]
                self._event("BLOCKED", t["id"], reason="autonomy", detail=gate["reason"],
                            level=gate["level"], trigger=self.trigger)
                continue
            owns = self.owns_of(t)
            if any(_overlap(owns, h) for h in held):
                waiting.append(t["id"])
                continue
            trigger = "retry-fresh" if self.attempts.get(t["id"]) else "fan-out"
            try:
                if self.repo and t["id"] not in self.worktrees:
                    self.worktrees[t["id"]] = gitops.worktree_for(self.repo, t)
                contract = self.build_contract(t, trigger)
            except gitops.MergeConflict as e:   # nhiều dep, gộp nhánh xung đột → blocked, lý do rõ
                self._block(t["id"], f"xung đột merge nhánh deps {e.branches}: {e.files}")
                continue
            except Exception as e:  # contract sai schema / git lỗi → không spawn, lý do vào blocked_reason
                self._block(t["id"], f"không spawn được: {e}")
                continue
            try:
                alloc = self._allocate()
            except ledger.BudgetExceeded as e:
                self._over_budget([x["id"] for x in ready if x["id"] not in busy], str(e))
                break
            handle = WorkerHandle(self, t["id"])
            handle.board = WorkerBoard(self.board, self.worker_agents,
                                       cancelled=lambda h=handle: h.cancelled.is_set(), lock=self._lock)
            self._set_status(t["id"], "in_dev")
            self._event("ASSIGN", t["id"], agent=agent, task_type=task_type, trigger=trigger)
            self._event("SPAWN", t["id"], trigger=trigger, owns=owns,
                        attempt=contract["input_slice"]["attempt"], budget=contract["budget"])
            fut = self._spawn(contract, handle, alloc)
            running[fut] = (t["id"], owns, time.monotonic() + self.ttl, handle)
            held.append(owns)
            busy.add(t["id"])
            spawned += 1
        return waiting, spawned

    def _reap(self, running: Running) -> None:
        """Chờ worker xong (tối đa tới deadline gần nhất); worker quá ttl → KILL + blocked."""
        now = time.monotonic()
        timeout = max(0.0, min(d for _, _, d, _ in running.values()) - now)
        done, _ = cf.wait(list(running), timeout=timeout, return_when=cf.FIRST_COMPLETED)
        for f in done:
            self._on_result(f, *running.pop(f))
        now = time.monotonic()
        for f, (tid, owns, deadline, handle) in list(running.items()):
            if now < deadline:
                continue
            running.pop(f)
            with self._lock:                         # cancel + block nguyên tử với report()
                handle.cancelled.set()
                zombie = registry.zombify(handle, self.scope, owns)
                if zombie:
                    self._event("KILL", tid, reason=f"quá ttl {self.ttl}s")
                    self._block(tid, "KILL: quá ttl")
            if not zombie:                           # vừa kịp xong đúng lúc hết ttl
                cf.wait([f])
                self._on_result(f, tid, owns, deadline, handle)

    def _wait_zombies(self, zombies: list[dict]) -> bool:
        """Chờ tối đa kill_grace_s cho một zombie dừng. True nếu có zombie đã dừng."""
        deadline = time.monotonic() + self.kill_grace_s
        while time.monotonic() < deadline:
            if any(z["done"].is_set() for z in zombies):
                return True
            time.sleep(0.01)
        return any(z["done"].is_set() for z in zombies)
