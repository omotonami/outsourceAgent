"""Dispatcher — trạng thái + thao tác: ghi substate, sự kiện, contract, budget, thread worker, kết quả.

Vòng chọn/spawn/KILL nằm ở scheduler.py. Mọi ghi Blackboard đi qua lock và đúng writer zone
(AGENT = orchestrator). Sự kiện qua events.emit.
"""
from __future__ import annotations

import concurrent.futures as cf
import threading
from pathlib import Path
from typing import Callable

from jsonschema import validate

from src.gateway import budget as ledger

from .. import escalation, events
from . import gitops, registry
from .dag import default_owns
from .registry import DepthExceeded
from .scheduler import Scheduler
from .settings import (AGENT, ANTI_JOB, BLOCKED, CONTRACT_SCHEMA, DONE, PARENT, PHASE, SUBSTATES, cfg,
                       worker_agents)
from .worker import Worker, WorkerHandle, default_pipeline


class Dispatcher(Scheduler):
    """Một lần phát task_backlog của một dự án. `run()` (scheduler.py) trả tóm tắt kết quả."""

    def __init__(self, board, worker: Worker | None = None, *, max_parallel: int | None = None,
                 worker_tokens: int | None = ..., ttl: float | None = None,
                 owns_of: Callable[[dict], list[str]] = default_owns,
                 repo: str | Path | None = None, output_schema: str = "specs/04_schemas/c3_qc.out.json",
                 kill_grace_s: float | None = None, trigger: str = "manual", autonomy=None):
        project_id = getattr(board, "project_id", None) or "p"
        registry.check_depth("tạo", project_id)
        self.board = board
        self.project_id = project_id
        self.scope = self.project_id
        self.worker = worker or default_pipeline()
        self.worker_agents = worker_agents()
        self.max_parallel = int(cfg("max_parallel") if max_parallel is None else max_parallel)
        if self.max_parallel < 1:
            raise ValueError("max_parallel phải ≥ 1")
        self.worker_tokens = cfg("worker_tokens", nullable=True) if worker_tokens is ... else worker_tokens
        self.ttl = float(cfg("worker_ttl_s") if ttl is None else ttl)
        self.kill_grace_s = float(cfg("kill_grace_s") if kill_grace_s is None else kill_grace_s)
        self.owns_of, self.repo, self.output_schema = owns_of, repo, output_schema
        self.trigger = trigger
        if autonomy is None:
            from ..autonomy import Autonomy
            autonomy = Autonomy()
        self.autonomy = autonomy
        self.autonomy_denied: dict[str, str] = {}
        self._lock = threading.RLock()
        self._used_start = ledger.used(self.project_id, PHASE)
        self.escalation: str | None = None
        self.attempts: dict[str, int] = {}
        self.worktrees: dict[str, str] = {}
        self._live = 0
        self.max_concurrency = 0

    # ---------- Blackboard (mọi ghi đi qua lock, đúng writer zone) ----------
    def _backlog(self) -> list[dict]:
        return self.board.get("task_backlog") or []

    @property
    def used_tokens(self) -> int:
        return ledger.used(self.project_id, PHASE) - self._used_start

    def _set_status(self, task_id: str, status: str, _handle: WorkerHandle | None = None, **extra) -> None:
        if status == "merged":
            raise PermissionError("Dispatcher không được chuyển merged — chỉ sau G3 do người ký")
        if status not in SUBSTATES and status != BLOCKED:
            raise ValueError(f"substate lạ: {status}")
        with self._lock:
            if _handle is not None and _handle.cancelled.is_set():   # TOCTOU: KILL xảy ra sau lần kiểm đầu
                return
            tb = self._backlog()
            for t in tb:
                if t["id"] == task_id:
                    t["status"] = status
                    t.update(extra)
            self.board.write("task_backlog", tb, agent=AGENT)

    def _event(self, kind: str, task_id: str | None, /, **fields) -> None:
        with self._lock:
            events.emit(self.board, kind, writer=AGENT, by=PARENT, task_id=task_id, **fields)

    def _block(self, task_id: str, reason: str) -> None:
        self._set_status(task_id, BLOCKED, blocked_reason=reason)
        self._event("BLOCKED", task_id, reason=reason)

    # ---------- Contract ----------
    def build_contract(self, task: dict, trigger: str) -> dict:
        slim = {k: v for k, v in task.items() if k not in ("status", "blocked_reason")}
        contract = {
            "task_id": task["id"], "parent": PARENT, "trigger": trigger,
            "goal": task.get("spec") or "",
            "input_slice": {"task": slim, "spec": task.get("spec", ""), "AC": task.get("AC") or [],
                            "risk_level": task.get("risk_level", "normal"),
                            "worktree": self.worktrees.get(task["id"]),
                            "attempt": self.attempts.get(task["id"], 0) + 1},
            "owns": self.owns_of(task),
            "output_schema": self.output_schema,
            "done_criteria": list(task.get("AC") or []) or ["c3_qc verdict = pass"],
            "anti_job": ANTI_JOB,
            "budget": {"tokens": self.worker_tokens},
            "ttl": self.ttl,
            "return_to": PARENT,
        }
        validate(contract, CONTRACT_SCHEMA)
        return contract

    # ---------- Budget (sổ chung của gateway) ----------
    def _allocate(self):
        """Giữ chỗ worker_tokens trên sổ gateway. None nếu không cấu hình worker_tokens.
        Hết chỗ → BudgetExceeded."""
        if self.worker_tokens:
            return ledger.reserve(self.project_id, PHASE, self.worker_tokens)
        rem = ledger.remaining(self.project_id, PHASE)
        if rem is not None and rem <= 0:
            raise ledger.BudgetExceeded(f"{self.project_id}/{PHASE} hết budget")
        return None

    def _over_budget(self, pending: list[str], detail: str) -> None:
        self.escalation = escalation.should_escalate(0, "", failed=False, over_budget=True)
        self.board.append("risk_register", {"risk": f"Dispatcher: {self.escalation}",
                          "severity": "High", "status": "open"}, agent=AGENT)
        self._event("BLOCKED", None, reason=self.escalation, detail=detail, pending=pending)

    # ---------- Thread worker ----------
    def _spawn(self, contract: dict, handle: WorkerHandle, alloc) -> cf.Future:
        """Thread daemon (không ThreadPoolExecutor): zombie không làm treo tiến trình lúc thoát."""
        fut: cf.Future = cf.Future()
        fut.set_running_or_notify_cancel()
        registry.worker_started(self.scope)
        with self._lock:
            self._live += 1
            self.max_concurrency = max(self.max_concurrency, self._live)

        def main():
            ident = registry.thread_enter()
            res, exc = None, None
            try:
                with ledger.use_allocation(alloc):
                    res = self.worker(contract, handle)
            except BaseException as e:  # noqa: BLE001 — chuyển cho Dispatcher qua Future
                exc = e
            finally:
                ledger.release(alloc)
                registry.thread_exit(ident, self.scope, handle)
                with self._lock:
                    self._live -= 1
                if exc is not None:
                    fut.set_exception(exc)
                else:
                    fut.set_result(res)

        threading.Thread(target=main, name=f"c-worker-{handle.task_id}", daemon=True).start()
        return fut

    def _on_result(self, fut: cf.Future, tid: str, owns, deadline, handle) -> None:
        try:
            res = fut.result() or {}
            err = None if res.get("ok") else (res.get("reason") or res.get("error") or "worker fail")
        except DepthExceeded as e:
            res, err = {}, f"vi phạm depth: {e}"
        except ledger.BudgetExceeded as e:
            # Hết budget giữa chừng: không tính là một vòng retry; task về ready, ngừng spawn.
            self._event("RESULT", tid, ok=False, reason=f"BudgetExceeded: {e}",
                        attempt=self.attempts.get(tid, 0) + 1)
            self._set_status(tid, "ready")
            if not self.escalation:
                self._over_budget([tid], str(e))
            return
        except Exception as e:  # lỗi bất kỳ của worker = một lần fail (retry/blocked bên dưới)
            res, err = {}, f"{type(e).__name__}: {e}"
        used = res.get("tokens_used")
        self.attempts[tid] = self.attempts.get(tid, 0) + 1
        self._event("RESULT", tid, ok=err is None, reason=err, attempt=self.attempts[tid],
                    tokens_used=used, qc_critical=bool(res.get("qc_critical")))
        if err is None:
            try:
                gitops.commit_worktree(self.worktrees.get(tid), tid)
            except Exception as e:  # git lỗi → không coi là xong
                self._block(tid, f"không commit được worktree: {e}")
                return
            self._set_status(tid, "await_merge", qc_critical=bool(res.get("qc_critical")))
            return
        # Retry: còn vòng → về ready (retry-fresh); hết vòng / Opus vẫn fail → blocked.
        reason = escalation.should_escalate(self.attempts[tid] + 1, res.get("tier", ""),
                                            failed=True, over_budget=False)
        if reason:
            self._block(tid, f"{reason}: {err}")
        else:
            self._set_status(tid, "ready")

    def _summary(self, spawned: int) -> dict:
        tb = self._backlog()
        done = [t["id"] for t in tb if t.get("status") in DONE]
        blocked = {t["id"]: t.get("blocked_reason") for t in tb if t.get("status") == BLOCKED}
        qc_critical = any(t.get("qc_critical") for t in tb)
        br = self.board.get("build_results") or {}
        br.update({"all_checks_pass": bool(tb) and len(done) == len(tb) and not qc_critical,
                   "qc_critical": qc_critical,
                   "loops": max(self.attempts.values(), default=0)})
        self.board.write("build_results", br, agent=AGENT)
        return {"spawned": spawned, "done": done, "blocked": blocked,
                "pending": [t["id"] for t in tb if t["id"] not in done and t["id"] not in blocked],
                "escalation": self.escalation, "used_tokens": self.used_tokens,
                "max_concurrency": self.max_concurrency, "worktrees": dict(self.worktrees),
                "autonomy_denied": dict(self.autonomy_denied)}
