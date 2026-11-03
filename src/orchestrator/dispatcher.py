"""H2 Build Dispatcher — phát task_backlog (B4) cho nhiều worker chạy song song.

CODE thuần, không LLM quyết thứ tự hay số worker:
- Thứ tự: DAG `deps` (kiểm không vòng, dep phải tồn tại). Task ready = mọi dep đã `await_merge`/`merged`.
- Song song: concurrent.futures, tối đa `dispatcher.max_parallel` (config/budgets.yaml).
- Mỗi worker một Spawn Contract hợp lệ theo specs/04_schemas/spawn_contract.json.
- `owns` của các worker đang chạy không giao nhau; giao → task chờ.
- Budget worker trừ từ budget phase build; vượt → ngừng spawn (escalation.should_escalate).
- Substate: ready → in_dev → testing → qc → await_merge. KHÔNG BAO GIỜ ghi `merged`
  (chỉ sau G3 do người ký). Retry ≤ max_build_loops; fail → blocked.
- Depth tối đa 1: gọi Dispatcher từ trong worker → DepthExceeded.
- Sự kiện SPAWN / RESULT / BLOCKED / KILL ghi append-only vào zone `trace_log`.
- H4 Autonomy: trước khi spawn hỏi `autonomy.can_run(agent, task_type, trigger)`. Không được phép →
  task GIỮ `ready` + sự kiện BLOCKED reason="autonomy". `trigger` mặc định "manual" (người gọi run()).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import threading
import time
from pathlib import Path
from typing import Callable

import yaml
from jsonschema import validate

from . import escalation

ROOT = Path(__file__).parents[2]
_BUDGETS = yaml.safe_load((ROOT / "config/budgets.yaml").read_text(encoding="utf-8"))
_TRANS = yaml.safe_load((Path(__file__).parent / "transitions.yaml").read_text(encoding="utf-8"))
SUBSTATES: list[str] = _TRANS["task_substates"]
CONTRACT_SCHEMA = json.loads((ROOT / "specs/04_schemas/spawn_contract.json").read_text(encoding="utf-8"))

AGENT = "orchestrator"                       # writer của task_backlog / build_results / trace_log
PARENT = "orchestrator.dispatcher"
DONE = {"await_merge", "merged"}             # dep coi là xong (merged chỉ do G3 ngoài dispatcher)
WORKER_STATES = ("in_dev", "testing", "qc")  # worker được tự báo trong khoảng này
BLOCKED = "blocked"                          # trạng thái ngoài luồng, không thuộc task_substates
EXCLUSIVE = "*"                              # owns mặc định: giữ độc quyền repo → chạy tuần tự
ANTI_JOB = [
    "Không gọi Dispatcher / không spawn worker khác (depth tối đa 1)",
    "Không sửa file ngoài owns",
    "Không merge, không push, không mở PR",
    "Không tự chuyển task sang merged (chỉ sau G3 do người ký)",
]

_LOCAL = threading.local()


class DepthExceeded(Exception):
    pass


class CycleError(ValueError):
    pass


def _cfg(key, default):
    v = (_BUDGETS.get("dispatcher") or {}).get(key)
    return default if v is None else v


def check_dag(tasks: list[dict]) -> list[str]:
    """Kiểm DAG; trả thứ tự topo. Id trùng / dep không tồn tại / vòng → CycleError."""
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise CycleError("task id trùng")
    known = set(ids)
    indeg = {i: 0 for i in ids}
    children: dict[str, list[str]] = {i: [] for i in ids}
    for t in tasks:
        for d in t.get("deps") or []:
            if d not in known:
                raise CycleError(f"{t['id']} phụ thuộc task không tồn tại: {d}")
            indeg[t["id"]] += 1
            children[d].append(t["id"])
    queue = [i for i in ids if indeg[i] == 0]
    order = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(ids):
        raise CycleError(f"deps có vòng: {sorted(set(ids) - set(order))}")
    return order


def _overlap(a: list[str], b: list[str]) -> bool:
    if EXCLUSIVE in a or EXCLUSIVE in b:
        return True
    for x in a:
        for y in b:
            if x == y or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/"):
                return True
    return False


def default_owns(task: dict) -> list[str]:
    """B4 chưa khai owns trong schema → mặc định độc quyền (an toàn, chạy tuần tự)."""
    return list(task.get("owns") or [EXCLUSIVE])


class WorkerHandle:
    """Cái duy nhất worker nhận ngoài contract: báo substate + biết khi bị KILL."""

    def __init__(self, dispatcher: "Dispatcher", task_id: str):
        self._d, self.task_id = dispatcher, task_id
        self.cancelled = threading.Event()

    def report(self, substate: str) -> None:
        if substate not in WORKER_STATES:
            raise PermissionError(f"worker chỉ được báo {WORKER_STATES}, không được '{substate}'")
        if self.cancelled.is_set():
            return
        self._d._set_status(self.task_id, substate)


Worker = Callable[[dict, WorkerHandle], dict]


class Dispatcher:
    def __init__(self, board, worker: Worker | None = None, *, max_parallel: int | None = None,
                 phase_budget: int | None = ..., worker_tokens: int | None = ...,
                 ttl: float | None = None, owns_of: Callable[[dict], list[str]] = default_owns,
                 repo: str | Path | None = None, output_schema: str = "specs/04_schemas/c3_qc.out.json",
                 kill_grace_s: float = 5.0, trigger: str = "manual", autonomy=None):
        if getattr(_LOCAL, "in_worker", False):
            raise DepthExceeded("worker không được tạo Dispatcher (depth tối đa 1)")
        self.board = board
        self.worker = worker or default_pipeline(board)
        self.max_parallel = int(max_parallel or _cfg("max_parallel", 3))
        if self.max_parallel < 1:
            raise ValueError("max_parallel phải ≥ 1")
        self.phase_budget = (_BUDGETS["per_project_tokens"].get("build")
                             if phase_budget is ... else phase_budget)
        self.worker_tokens = _cfg("worker_tokens", None) if worker_tokens is ... else worker_tokens
        self.ttl = float(ttl or _cfg("worker_ttl_s", 1800))
        self.owns_of, self.repo, self.output_schema = owns_of, repo, output_schema
        self.kill_grace_s = kill_grace_s
        self.trigger = trigger
        if autonomy is None:
            from .autonomy import Autonomy
            autonomy = Autonomy()
        self.autonomy = autonomy
        self.autonomy_denied: dict[str, str] = {}
        self._lock = threading.RLock()
        self.used_tokens = 0
        self.escalation: str | None = None
        self.attempts: dict[str, int] = {}
        self.worktrees: dict[str, str] = {}
        self._live = 0
        self.max_concurrency = 0

    # ---------- Blackboard (mọi ghi đi qua lock, đúng writer zone) ----------
    def _backlog(self) -> list[dict]:
        return self.board.get("task_backlog") or []

    def _set_status(self, task_id: str, status: str, **extra) -> None:
        if status == "merged":
            raise PermissionError("Dispatcher không được chuyển merged — chỉ sau G3 do người ký")
        if status not in SUBSTATES and status != BLOCKED:
            raise ValueError(f"substate lạ: {status}")
        with self._lock:
            tb = self._backlog()
            for t in tb:
                if t["id"] == task_id:
                    t["status"] = status
                    t.update(extra)
            self.board.write("task_backlog", tb, agent=AGENT)

    def _event(self, kind: str, task_id: str | None, **fields) -> None:
        with self._lock:
            self.board.append("trace_log", {"event": kind, "task_id": task_id, "ts": time.time(),
                                            "by": PARENT, **fields}, agent=AGENT)

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

    # ---------- Budget ----------
    def _over_budget(self) -> bool:
        if self.phase_budget is None:
            return False
        need = self.worker_tokens or 0
        return self.used_tokens + need > self.phase_budget or self.used_tokens >= self.phase_budget

    # ---------- Chạy ----------
    def _run_worker(self, contract: dict, handle: WorkerHandle) -> dict:
        _LOCAL.in_worker = True
        try:
            with self._lock:
                self._live += 1
                self.max_concurrency = max(self.max_concurrency, self._live)
            return self.worker(contract, handle)
        finally:
            with self._lock:
                self._live -= 1
            _LOCAL.in_worker = False

    def _block(self, task_id: str, reason: str) -> None:
        self._set_status(task_id, BLOCKED, blocked_reason=reason)
        self._event("BLOCKED", task_id, reason=reason)

    def run(self) -> dict:
        if getattr(_LOCAL, "in_worker", False):
            raise DepthExceeded("worker không được gọi Dispatcher (depth tối đa 1)")
        tasks = self._backlog()
        check_dag(tasks)
        # Trạng thái dở từ lần chạy trước (không phải xong/blocked) → làm lại từ đầu.
        for t in tasks:
            if t.get("status") in WORKER_STATES:
                self._set_status(t["id"], "ready")

        running: dict[cf.Future, tuple[str, list[str], float, WorkerHandle]] = {}
        zombies: dict[cf.Future, tuple[str, list[str]]] = {}
        spawned = 0
        pool = cf.ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="c-worker")
        try:
            while True:
                tb = {t["id"]: t for t in self._backlog()}
                # dep blocked → task phụ thuộc cũng blocked
                for t in tb.values():
                    if t.get("status") in DONE or t.get("status") == BLOCKED:
                        continue
                    bad = [d for d in t.get("deps") or [] if tb[d].get("status") == BLOCKED]
                    if bad and t.get("status") in (None, "ready"):
                        self._block(t["id"], f"dep blocked: {bad}")
                tb = {t["id"]: t for t in self._backlog()}
                busy = {tid for tid, *_ in running.values()} | {tid for tid, _ in zombies.values()}
                ready = [t for t in tb.values()
                         if t.get("status") in (None, "ready") and t["id"] not in busy
                         and all(tb[d].get("status") in DONE for d in t.get("deps") or [])]
                for t in ready:
                    if t.get("status") is None:
                        self._set_status(t["id"], "ready")

                held = [o for _, o, *_ in running.values()] + [o for _, o in zombies.values()]
                waiting_owns = []
                for t in ready:
                    if self.escalation:
                        break
                    if len(running) + len(zombies) >= self.max_parallel:
                        break
                    if self._over_budget():
                        self.escalation = escalation.should_escalate(0, "", failed=False, over_budget=True)
                        self.board.append("risk_register", {"risk": f"Dispatcher: {self.escalation}",
                                          "severity": "High", "status": "open"}, agent=AGENT)
                        self._event("BLOCKED", None, reason=self.escalation,
                                    pending=[x["id"] for x in ready if x["id"] not in busy])
                        break
                    if t["id"] in self.autonomy_denied:
                        continue
                    gate = self.autonomy.can_run(t.get("agent") or "c1_dev", t.get("type") or "build_task",
                                                 self.trigger)
                    if not gate["allowed"]:  # giữ ready, chỉ báo BLOCKED một lần mỗi run
                        self.autonomy_denied[t["id"]] = gate["reason"]
                        self._event("BLOCKED", t["id"], reason="autonomy", detail=gate["reason"],
                                    level=gate["level"], trigger=self.trigger)
                        continue
                    owns = self.owns_of(t)
                    if any(_overlap(owns, h) for h in held):
                        waiting_owns.append(t["id"])
                        continue
                    trigger = "retry-fresh" if self.attempts.get(t["id"]) else "fan-out"
                    try:
                        if self.repo and t["id"] not in self.worktrees:
                            from src.tools.integrations import repo as git
                            self.worktrees[t["id"]] = str(git.create_worktree(self.repo, t["id"]))
                        contract = self.build_contract(t, trigger)
                    except Exception as e:  # contract sai / git lỗi → không spawn
                        self._block(t["id"], f"không spawn được: {e}")
                        continue
                    with self._lock:
                        self.used_tokens += self.worker_tokens or 0
                    handle = WorkerHandle(self, t["id"])
                    self._set_status(t["id"], "in_dev")
                    self._event("SPAWN", t["id"], trigger=trigger, owns=owns,
                                attempt=contract["input_slice"]["attempt"], budget=contract["budget"])
                    fut = pool.submit(self._run_worker, contract, handle)
                    running[fut] = (t["id"], owns, time.monotonic() + self.ttl, handle)
                    held.append(owns)
                    busy.add(t["id"])
                    spawned += 1

                if not running:
                    if zombies and waiting_owns:  # chỉ còn worker đã KILL giữ owns
                        done, _ = cf.wait(list(zombies), timeout=self.kill_grace_s)
                        if done:
                            for f in done:
                                zombies.pop(f)
                            continue
                        self.escalation = self.escalation or "worker bị KILL chưa dừng, owns còn bị giữ"
                    break

                now = time.monotonic()
                timeout = max(0.0, min(d for _, _, d, _ in running.values()) - now)
                done, _ = cf.wait(list(running) + list(zombies), timeout=timeout,
                                  return_when=cf.FIRST_COMPLETED)
                for f in list(zombies):
                    if f in done:
                        zombies.pop(f)
                for f in done:
                    if f in running:
                        self._on_result(f, *running.pop(f))
                now = time.monotonic()
                for f, (tid, owns, deadline, handle) in list(running.items()):
                    if now >= deadline:
                        handle.cancelled.set()
                        running.pop(f)
                        zombies[f] = (tid, owns)
                        self._event("KILL", tid, reason=f"quá ttl {self.ttl}s")
                        self._block(tid, "KILL: quá ttl")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        return self._summary(spawned)

    def _on_result(self, fut: cf.Future, tid: str, owns, deadline, handle) -> None:
        try:
            res = fut.result() or {}
            err = None if res.get("ok") else (res.get("reason") or res.get("error") or "worker fail")
        except DepthExceeded as e:
            res, err = {}, f"vi phạm depth: {e}"
        except Exception as e:
            res, err = {}, f"{type(e).__name__}: {e}"
        used = res.get("tokens_used")
        if used is not None:
            with self._lock:
                self.used_tokens += int(used) - (self.worker_tokens or 0)
        self.attempts[tid] = self.attempts.get(tid, 0) + 1
        self._event("RESULT", tid, ok=err is None, reason=err, attempt=self.attempts[tid],
                    tokens_used=used, qc_critical=bool(res.get("qc_critical")))
        if err is None:
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


def default_pipeline(board, user_id: str = "dispatcher", ctx: dict | None = None) -> Worker:
    """Worker mặc định: c1_dev → c2_tester → c3_qc (gọi agent qua runtime/gateway).
    Pass khi c3_qc verdict = pass. Cần API key khi chạy thật; test dùng worker mock."""
    def worker(contract: dict, h: WorkerHandle) -> dict:
        from src.agents import c1_dev, c2_tester, c3_qc
        sl = contract["input_slice"]
        c = {"project_id": getattr(board, "project_id", "p"), "risk_level": sl.get("risk_level", "normal"),
             **(ctx or {})}
        r1 = c1_dev.run(board, user_id, c, {"task": sl["task"], "spec": sl["spec"], "AC": sl["AC"]})
        if not r1.get("ok"):
            return {"ok": False, "reason": f"c1_dev: {r1.get('reason')}", "tier": r1.get("tier", "")}
        h.report("testing")
        r2 = c2_tester.run(board, user_id, c, {"AC": sl["AC"], "pr": r1["data"]})
        if not r2.get("ok"):
            return {"ok": False, "reason": f"c2_tester: {r2.get('reason')}", "tier": r2.get("tier", "")}
        h.report("qc")
        pr_diff = ""
        if sl.get("worktree"):
            from src.tools.integrations import repo as git
            pr_diff = git.diff(sl["worktree"])
        r3 = c3_qc.run(board, user_id, c, {"pr_diff": pr_diff or json.dumps(r1["data"], ensure_ascii=False),
                                           "spec": sl["spec"], "AC": sl["AC"]})
        if not r3.get("ok"):
            return {"ok": False, "reason": f"c3_qc: {r3.get('reason')}", "tier": r3.get("tier", "")}
        crit = any(i.get("severity") == "Critical" for i in r3["data"].get("issues", []))
        ok = r3["data"].get("verdict") == "pass" and not crit
        return {"ok": ok, "reason": None if ok else "c3_qc verdict fail", "qc_critical": crit,
                "tier": r3.get("tier", ""), "data": {"dev": r1["data"], "tests": r2["data"], "qc": r3["data"]}}
    return worker
