"""H2 Build Dispatcher — phát task_backlog (B4) cho nhiều worker chạy song song.

CODE thuần, không LLM quyết thứ tự hay số worker:
- Thứ tự: DAG `deps` (kiểm không vòng, dep phải tồn tại). Task ready = mọi dep đã `await_merge`/`merged`.
- Song song: concurrent.futures, tối đa `dispatcher.max_parallel` (config/budgets.yaml).
- Mỗi worker một Spawn Contract hợp lệ theo specs/04_schemas/spawn_contract.json.
- `owns` của các worker đang chạy không giao nhau; giao → task chờ.
- Budget: dùng CHUNG sổ gateway (src/gateway/budget.py). Mỗi worker giữ chỗ `worker_tokens` trên sổ
  phase build; mọi lần gọi model trong worker rút từ allocation đó. Hết chỗ → ngừng spawn.
- Substate: ready → in_dev → testing → qc → await_merge. KHÔNG BAO GIỜ ghi `merged`
  (chỉ sau G3 do người ký). Retry ≤ max_build_loops; fail → blocked.
- Depth tối đa 1: sổ cấp module (có lock) đếm worker đang chạy trong tiến trình; còn worker sống
  (kể cả thread con do worker mở) thì tạo/chạy Dispatcher → DepthExceeded.
- Worker bị KILL (quá ttl) thành zombie trong sổ cấp module: owns của nó vẫn bị giữ với MỌI run()
  sau cùng dự án cho tới khi thread thật sự dừng. Thread worker là daemon → không treo lúc thoát.
- Worker nhận board proxy (WorkerBoard): chỉ đọc + ghi đúng zone của agent con; không có sign/revoke.
- Số (max_parallel, ttl, kill_grace, worker_tokens) đọc từ config/budgets.yaml; thiếu → ConfigError.
- Sự kiện SPAWN / RESULT / BLOCKED / KILL ghi append-only vào zone `trace_log`.
- Git (khi có `repo`): worktree task con tạo từ nhánh task/<dep>; nhiều dep → nhánh tích hợp tạm
  integ/<id> (xung đột merge → task blocked). Task xong được commit cục bộ trên task/<id>.
- H4 Autonomy: trước khi spawn hỏi `autonomy.can_run(agent, task_type, trigger)`. Không được phép →
  task GIỮ `ready` + sự kiện BLOCKED reason="autonomy". `trigger` mặc định "manual" (người gọi run()).
"""
from __future__ import annotations

import concurrent.futures as cf
import fnmatch
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

import yaml
from jsonschema import validate

from src.blackboard.store import PermissionDenied
from src.gateway import budget as ledger
from src.tools.integrations.repo import MergeConflict as _MergeConflict

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

PHASE = "build"


class DepthExceeded(Exception):
    pass


class CycleError(ValueError):
    pass


class ConfigError(KeyError):
    pass


def _cfg(key, nullable=False):
    """Đọc dispatcher.<key> trong config/budgets.yaml. Không có default hard-code."""
    sec = _BUDGETS.get("dispatcher")
    if not isinstance(sec, dict) or key not in sec:
        raise ConfigError(f"config/budgets.yaml: thiếu 'dispatcher.{key}'")
    v = sec[key]
    if v is None and not nullable:
        raise ConfigError(f"config/budgets.yaml: 'dispatcher.{key}' không được null")
    return v


# ---------- Sổ cấp module: depth guard + zombie ----------
_REG = threading.Lock()
_ACTIVE = 0                        # worker đang chạy (chưa bị KILL) trong tiến trình
_WORKER_THREADS: set[int] = set()  # ident thread worker còn sống (kể cả zombie)
_ZOMBIES: dict[int, dict] = {}     # id(handle) -> {scope, task_id, owns, done: Event}


def _check_depth(where: str) -> None:
    with _REG:
        if _ACTIVE > 0 or threading.get_ident() in _WORKER_THREADS:
            raise DepthExceeded(f"worker không được {where} Dispatcher (depth tối đa 1; "
                                f"{_ACTIVE} worker đang chạy)")


def live_zombies(scope=None) -> list[dict]:
    """Worker đã KILL nhưng thread chưa dừng (scope=None: mọi dự án)."""
    with _REG:
        return [dict(z) for z in _ZOMBIES.values() if scope is None or z["scope"] == scope]


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


_GLOB = re.compile(r"[*?\[]")


def _norm(p: str) -> str:
    p = p.strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_overlap(x: str, y: str) -> bool:
    x, y = _norm(x), _norm(y)
    gx, gy = _GLOB.search(x), _GLOB.search(y)
    if gx or gy:
        # glob: thận trọng — so phần tĩnh trước ký tự glob đầu tiên (có thể báo giao thừa, không sót)
        if (gx and fnmatch.fnmatchcase(y, x)) or (gy and fnmatch.fnmatchcase(x, y)):
            return True
        px = x[:gx.start()] if gx else x
        py = y[:gy.start()] if gy else y
        return px.startswith(py) or py.startswith(px)
    return x == y or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/")


def _overlap(a: list[str], b: list[str]) -> bool:
    if EXCLUSIVE in a or EXCLUSIVE in b:
        return True
    return any(_path_overlap(x, y) for x in a for y in b)


def default_owns(task: dict) -> list[str]:
    """`owns` từ task_backlog (field của B4, specs/04_schemas/b4_tech_lead.out.json).
    Thiếu/rỗng → ["*"] = độc quyền (an toàn, chạy tuần tự)."""
    return list(task.get("owns") or [EXCLUSIVE])


def owns_conflicts(tasks: list[dict]) -> list[tuple[str, str]]:
    """Cặp task có thể chạy song song (không task nào là tổ tiên của task kia) mà owns giao nhau.
    Kiểm Done-when của B4 bằng Code; task thiếu owns bị bỏ qua (Dispatcher đã chạy tuần tự)."""
    check_dag(tasks)
    deps = {t["id"]: set(t.get("deps") or []) for t in tasks}
    anc: dict[str, set[str]] = {}

    def ancestors(i):
        if i not in anc:
            anc[i] = set()
            for d in deps[i]:
                anc[i] |= {d} | ancestors(d)
        return anc[i]

    out = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            if not a.get("owns") or not b.get("owns"):
                continue
            if a["id"] in ancestors(b["id"]) or b["id"] in ancestors(a["id"]):
                continue
            if _overlap(a["owns"], b["owns"]):
                out.append((a["id"], b["id"]))
    return out


def worker_agents() -> list[str]:
    """Agent con được chạy trong worker = subordinates của B4 (specs/02_agents/b4_tech_lead.md)."""
    text = (ROOT / "specs/02_agents/b4_tech_lead.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---", 2)[1]) or {}
    subs = meta.get("subordinates")
    if not subs:
        raise ConfigError("specs/02_agents/b4_tech_lead.md: thiếu 'subordinates'")
    return list(subs)


class WorkerBoard:
    """Board proxy cho worker: đọc được; ghi/append chỉ với danh tính agent con (specs/05_blackboard.yaml
    vẫn quyết zone). KHÔNG có sign / revoke_signature / ghi task_backlog / trace_log."""
    __slots__ = ("_WorkerBoard__b", "_WorkerBoard__agents", "project_id")

    def __init__(self, board, agents):
        self.__b, self.__agents = board, frozenset(agents)
        self.project_id = getattr(board, "project_id", None)

    def get(self, zone):
        return self.__b.get(zone)

    def is_signed(self, gate):
        return self.__b.is_signed(gate)

    def _who(self, agent):
        if agent not in self.__agents:
            raise PermissionDenied(f"worker chỉ ghi với danh tính {sorted(self.__agents)}, không phải {agent!r}")

    def write(self, zone, value, agent):
        self._who(agent)
        return self.__b.write(zone, value, agent=agent)

    def append(self, zone, entry, agent):
        self._who(agent)
        return self.__b.append(zone, entry, agent=agent)


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


class Dispatcher:
    def __init__(self, board, worker: Worker | None = None, *, max_parallel: int | None = None,
                 worker_tokens: int | None = ..., ttl: float | None = None,
                 owns_of: Callable[[dict], list[str]] = default_owns,
                 repo: str | Path | None = None, output_schema: str = "specs/04_schemas/c3_qc.out.json",
                 kill_grace_s: float | None = None, trigger: str = "manual", autonomy=None):
        _check_depth("tạo")
        self.board = board
        self.project_id = getattr(board, "project_id", None) or "p"
        self.scope = self.project_id
        self.worker = worker or default_pipeline()
        self.worker_agents = worker_agents()
        self.max_parallel = int(_cfg("max_parallel") if max_parallel is None else max_parallel)
        if self.max_parallel < 1:
            raise ValueError("max_parallel phải ≥ 1")
        self.worker_tokens = _cfg("worker_tokens", nullable=True) if worker_tokens is ... else worker_tokens
        self.ttl = float(_cfg("worker_ttl_s") if ttl is None else ttl)
        self.kill_grace_s = float(_cfg("kill_grace_s") if kill_grace_s is None else kill_grace_s)
        self.owns_of, self.repo, self.output_schema = owns_of, repo, output_schema
        self.trigger = trigger
        if autonomy is None:
            from .autonomy import Autonomy
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

    def _set_status(self, task_id: str, status: str, _handle: "WorkerHandle | None" = None, **extra) -> None:
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

    # ---------- Git worktree ----------
    def _worktree_for(self, task: dict) -> str:
        """Worktree task/<id>. Task con tạo từ NHÁNH CỦA TASK CHA (task/<dep>); nhiều dep →
        nhánh tích hợp tạm integ/<id> gộp các nhánh dep (xung đột → MergeConflict → blocked).
        Nhánh task/<id> đã có (chạy lại) → gắn lại, không đổi base."""
        from src.tools.integrations import repo as git
        tid = task["id"]
        if git.branch_exists(self.repo, git.branch_name(tid)):
            return str(git.ensure_worktree(self.repo, tid))
        base = git.base_for_deps(self.repo, tid, list(task.get("deps") or []))
        try:
            return str(git.ensure_worktree(self.repo, tid, base=base))
        finally:
            git.drop_integ_branch(self.repo, tid)

    def _commit_worktree(self, tid: str) -> None:
        """Task đạt await_merge: chốt việc trong worktree thành commit cục bộ trên task/<id> để
        task con (tạo từ nhánh này) thấy được. Không merge, không push."""
        wt = self.worktrees.get(tid)
        if not wt:
            return
        from src.tools.integrations import repo as git
        if git.has_changes(wt):
            git.commit_all(wt, f"task {tid}: await_merge (dispatcher)")

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

    # ---------- Chạy ----------
    def _spawn(self, contract: dict, handle: WorkerHandle, alloc) -> cf.Future:
        """Thread daemon (không ThreadPoolExecutor): zombie không làm treo tiến trình lúc thoát."""
        global _ACTIVE
        fut: cf.Future = cf.Future()
        fut.set_running_or_notify_cancel()
        with _REG:
            _ACTIVE += 1
        with self._lock:
            self._live += 1
            self.max_concurrency = max(self.max_concurrency, self._live)

        def main():
            global _ACTIVE
            ident = threading.get_ident()
            with _REG:
                _WORKER_THREADS.add(ident)
            res, exc = None, None
            try:
                with ledger.use_allocation(alloc):
                    res = self.worker(contract, handle)
            except BaseException as e:  # noqa: BLE001 — chuyển cho Dispatcher qua Future
                exc = e
            finally:
                ledger.release(alloc)
                with _REG:
                    _WORKER_THREADS.discard(ident)
                    handle.finished = True
                    if handle.zombie:
                        z = _ZOMBIES.pop(id(handle), None)
                        if z:
                            z["done"].set()
                    else:
                        _ACTIVE -= 1
                with self._lock:
                    self._live -= 1
                if exc is not None:
                    fut.set_exception(exc)
                else:
                    fut.set_result(res)

        threading.Thread(target=main, name=f"c-worker-{handle.task_id}", daemon=True).start()
        return fut

    def _zombify(self, handle: WorkerHandle, owns: list[str]) -> bool:
        """Đánh dấu worker quá ttl là zombie (owns vẫn bị giữ). False nếu nó vừa kịp xong."""
        global _ACTIVE
        with _REG:
            if handle.finished:
                return False
            handle.zombie = True
            _ACTIVE -= 1
            _ZOMBIES[id(handle)] = {"scope": self.scope, "task_id": handle.task_id, "owns": list(owns),
                                    "done": threading.Event()}
        return True

    def _block(self, task_id: str, reason: str) -> None:
        self._set_status(task_id, BLOCKED, blocked_reason=reason)
        self._event("BLOCKED", task_id, reason=reason)

    def run(self) -> dict:
        _check_depth("chạy")
        tasks = self._backlog()
        check_dag(tasks)
        # Trạng thái dở từ lần chạy trước (không phải xong/blocked) → làm lại từ đầu.
        for t in tasks:
            if t.get("status") in WORKER_STATES:
                self._set_status(t["id"], "ready")

        running: dict[cf.Future, tuple[str, list[str], float, WorkerHandle]] = {}
        spawned = 0
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
            zombies = live_zombies(self.scope)          # gồm cả zombie của run() trước
            busy = {tid for tid, *_ in running.values()} | {z["task_id"] for z in zombies}
            ready = [t for t in tb.values()
                     if t.get("status") in (None, "ready") and t["id"] not in busy
                     and all(tb[d].get("status") in DONE for d in t.get("deps") or [])]
            for t in ready:
                if t.get("status") is None:
                    self._set_status(t["id"], "ready")

            held = [o for _, o, *_ in running.values()] + [z["owns"] for z in zombies]
            waiting = []                                 # chờ vì owns / chỗ bị zombie giữ
            for t in ready:
                if self.escalation:
                    break
                if len(running) + len(zombies) >= self.max_parallel:
                    waiting.append(t["id"])
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
                    waiting.append(t["id"])
                    continue
                trigger = "retry-fresh" if self.attempts.get(t["id"]) else "fan-out"
                try:
                    if self.repo and t["id"] not in self.worktrees:
                        self.worktrees[t["id"]] = self._worktree_for(t)
                    contract = self.build_contract(t, trigger)
                except _MergeConflict as e:   # nhiều dep, gộp nhánh xung đột → blocked, lý do rõ
                    self._block(t["id"], f"xung đột merge nhánh deps {e.branches}: {e.files}")
                    continue
                except Exception as e:  # contract sai / git lỗi → không spawn
                    self._block(t["id"], f"không spawn được: {e}")
                    continue
                try:
                    alloc = self._allocate()
                except ledger.BudgetExceeded as e:
                    self._over_budget([x["id"] for x in ready if x["id"] not in busy], str(e))
                    break
                handle = WorkerHandle(self, t["id"], WorkerBoard(self.board, self.worker_agents))
                self._set_status(t["id"], "in_dev")
                self._event("SPAWN", t["id"], trigger=trigger, owns=owns,
                            attempt=contract["input_slice"]["attempt"], budget=contract["budget"])
                fut = self._spawn(contract, handle, alloc)
                running[fut] = (t["id"], owns, time.monotonic() + self.ttl, handle)
                held.append(owns)
                busy.add(t["id"])
                spawned += 1

            if not running:
                if zombies and waiting and not self.escalation:
                    if self._wait_zombies(zombies):
                        continue
                    self.escalation = "worker bị KILL chưa dừng, owns còn bị giữ"
                    self._event("BLOCKED", None, reason=self.escalation, pending=waiting,
                                zombies=[z["task_id"] for z in zombies])
                break

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
                    zombie = self._zombify(handle, owns)
                    if zombie:
                        self._event("KILL", tid, reason=f"quá ttl {self.ttl}s")
                        self._block(tid, "KILL: quá ttl")
                if not zombie:                           # vừa kịp xong đúng lúc hết ttl
                    cf.wait([f])
                    self._on_result(f, tid, owns, deadline, handle)

        return self._summary(spawned)

    def _wait_zombies(self, zombies: list[dict]) -> bool:
        """Chờ tối đa kill_grace_s cho một zombie dừng. True nếu có zombie đã dừng."""
        deadline = time.monotonic() + self.kill_grace_s
        while time.monotonic() < deadline:
            if any(z["done"].is_set() for z in zombies):
                return True
            time.sleep(0.01)
        return any(z["done"].is_set() for z in zombies)

    def _over_budget(self, pending: list[str], detail: str) -> None:
        self.escalation = escalation.should_escalate(0, "", failed=False, over_budget=True)
        self.board.append("risk_register", {"risk": f"Dispatcher: {self.escalation}",
                          "severity": "High", "status": "open"}, agent=AGENT)
        self._event("BLOCKED", None, reason=self.escalation, detail=detail, pending=pending)

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
        except Exception as e:
            res, err = {}, f"{type(e).__name__}: {e}"
        used = res.get("tokens_used")
        self.attempts[tid] = self.attempts.get(tid, 0) + 1
        self._event("RESULT", tid, ok=err is None, reason=err, attempt=self.attempts[tid],
                    tokens_used=used, qc_critical=bool(res.get("qc_critical")))
        if err is None:
            try:
                self._commit_worktree(tid)
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
            return {"ok": False, "reason": f"c1_dev: {r1.get('reason')}", "tier": r1.get("tier", "")}
        h.report("testing")
        r2 = c2_tester.run(board, user_id, c, {"AC": sl["AC"], "pr": r1["data"]}, **kw)
        if not r2.get("ok"):
            return {"ok": False, "reason": f"c2_tester: {r2.get('reason')}", "tier": r2.get("tier", "")}
        h.report("qc")
        pr_diff = ""
        if sl.get("worktree"):
            from src.tools.integrations import repo as git
            pr_diff = git.diff(sl["worktree"])
        r3 = c3_qc.run(board, user_id, c, {"pr_diff": pr_diff or json.dumps(r1["data"], ensure_ascii=False),
                                           "spec": sl["spec"], "AC": sl["AC"]}, **kw)
        if not r3.get("ok"):
            return {"ok": False, "reason": f"c3_qc: {r3.get('reason')}", "tier": r3.get("tier", "")}
        crit = any(i.get("severity") == "Critical" for i in r3["data"].get("issues", []))
        ok = r3["data"].get("verdict") == "pass" and not crit
        return {"ok": ok, "reason": None if ok else "c3_qc verdict fail", "qc_critical": crit,
                "tier": r3.get("tier", ""), "data": {"dev": r1["data"], "tests": r2["data"], "qc": r3["data"]}}
    return worker
