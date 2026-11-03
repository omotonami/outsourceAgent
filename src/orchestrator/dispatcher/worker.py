"""Phía worker: WorkerHandle (thứ duy nhất worker nhận ngoài contract) + pipeline mặc định C1→C2→C3."""
from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Callable

from src.blackboard.proxy import WorkerBoard

from .settings import WORKER_STATES

if TYPE_CHECKING:
    from .core import Dispatcher


class WorkerHandle:
    """Cái duy nhất worker nhận ngoài contract: báo substate, board proxy, biết khi bị KILL."""

    def __init__(self, dispatcher: "Dispatcher", task_id: str, board: WorkerBoard | None = None):
        self._d, self.task_id = dispatcher, task_id
        self.cancelled = threading.Event()
        self.board = board
        self.zombie = False
        self.finished = False

    def report(self, substate: str) -> None:
        if substate not in WORKER_STATES:
            raise PermissionError(f"worker chỉ được báo {WORKER_STATES}, không được '{substate}'")
        if self.cancelled.is_set():
            return
        self._d._set_status(self.task_id, substate, _handle=self)   # kiểm lại cancelled trong lock


Worker = Callable[[dict, WorkerHandle], dict]


def _fail(step: str, r: dict) -> dict:
    return {"ok": False, "reason": f"{step}: {r.get('reason')}", "tier": r.get("tier", "")}


def default_pipeline(user_id: str = "dispatcher", ctx: dict | None = None, sender=None) -> Worker:
    """Worker mặc định: c1_dev → c2_tester → c3_qc (gọi agent qua runtime/gateway).
    Pass khi c3_qc verdict = pass. Chỉ dùng board proxy của handle (không sign, không ghi zone ngoài).
    Cần API key khi chạy thật; test dùng worker mock hoặc `sender` mock."""
    def worker(contract: dict, h: WorkerHandle) -> dict:
        from src.agents import c1_dev, c2_tester, c3_qc
        board = h.board
        sl = contract["input_slice"]
        c = {"project_id": getattr(board, "project_id", None) or "p",
             "risk_level": sl.get("risk_level", "normal"), **(ctx or {})}
        kw = {"sender": sender} if sender else {}
        r1 = c1_dev.run(board, user_id, c, {"task": sl["task"], "spec": sl["spec"], "AC": sl["AC"]}, **kw)
        if not r1.get("ok"):
            return _fail("c1_dev", r1)
        h.report("testing")
        r2 = c2_tester.run(board, user_id, c, {"AC": sl["AC"], "pr": r1["data"]}, **kw)
        if not r2.get("ok"):
            return _fail("c2_tester", r2)
        h.report("qc")
        pr_diff = ""
        if sl.get("worktree"):
            from src.tools.integrations import repo as git
            pr_diff = git.diff(sl["worktree"])
        r3 = c3_qc.run(board, user_id, c, {"pr_diff": pr_diff or json.dumps(r1["data"], ensure_ascii=False),
                                           "spec": sl["spec"], "AC": sl["AC"]}, **kw)
        if not r3.get("ok"):
            return _fail("c3_qc", r3)
        crit = any(i.get("severity") == "Critical" for i in r3["data"].get("issues", []))
        ok = r3["data"].get("verdict") == "pass" and not crit
        return {"ok": ok, "reason": None if ok else "c3_qc verdict fail", "qc_critical": crit,
                "tier": r3.get("tier", ""), "data": {"dev": r1["data"], "tests": r2["data"], "qc": r3["data"]}}
    return worker
