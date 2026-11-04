"""H2 Build Dispatcher — phát task_backlog (B4) cho nhiều worker chạy song song.

CODE thuần, không LLM quyết thứ tự hay số worker:
- Thứ tự: DAG `deps` (kiểm không vòng, dep phải tồn tại). Task ready = mọi dep đã `await_merge`/`merged`.
- Song song: thread daemon, tối đa `dispatcher.max_parallel` (config/budgets.yaml).
- Mỗi worker một Spawn Contract hợp lệ theo specs/04_schemas/spawn_contract.json.
- `owns` của các worker đang chạy không giao nhau; giao → task chờ.
- Budget: dùng CHUNG sổ gateway (src/gateway/budget.py). Mỗi worker giữ chỗ `worker_tokens` trên sổ
  phase build; mọi lần gọi model trong worker rút từ allocation đó. Hết chỗ → ngừng spawn.
- Substate: ready → in_dev → testing → qc → await_merge. KHÔNG BAO GIỜ ghi `merged`
  (chỉ sau G3 do người ký). Retry ≤ max_build_loops; fail → blocked.
- Depth tối đa 1, theo dự án (registry.py): worker và thread con của worker không được tạo/chạy
  Dispatcher nào; dự án còn worker đang chạy không tạo/chạy thêm Dispatcher; dự án khác chạy bình thường.
- Worker bị KILL (quá ttl) thành zombie: owns của nó vẫn bị giữ với MỌI run() sau cùng dự án cho tới khi
  thread thật sự dừng. Thread worker là daemon → không treo lúc thoát.
- Worker nhận board proxy (WorkerBoard, src/blackboard/proxy.py): chỉ đọc + ghi đúng zone của agent con.
- Số (max_parallel, ttl, kill_grace, worker_tokens) đọc từ config/budgets.yaml; thiếu → ConfigError.
- Sự kiện ASSIGN (giao task cho agent) / SPAWN / RESULT / BLOCKED / KILL ghi append-only vào zone
  `trace_log` (events.emit). ASSIGN ngay trước SPAWN, mỗi lần giao (kể cả retry).
- Git (khi có `repo`, gitops.py): worktree task con tạo từ nhánh task/<dep>; nhiều dep → nhánh tích hợp
  tạm integ/<id> (xung đột merge → task blocked). Task xong được commit cục bộ trên task/<id>.
- H4 Autonomy: trước khi spawn hỏi `autonomy.can_run(agent, task_type, trigger)`. Không được phép →
  task GIỮ `ready` + sự kiện BLOCKED reason="autonomy". `trigger` mặc định "manual" (người gọi run()).

Cấu trúc package (import path công khai `src.orchestrator.dispatcher` giữ nguyên):
  settings.py  hằng số + config     dag.py     DAG + owns        registry.py  depth guard + zombie
  worker.py    WorkerHandle + pipeline mặc định                  gitops.py    worktree/commit
  core.py      class Dispatcher (trạng thái, contract, budget, thread)   scheduler.py vòng chạy
"""
from src.blackboard.proxy import WorkerBoard
from src.gateway import budget as ledger

from .. import escalation
from .core import Dispatcher
from .dag import CycleError, _overlap, check_dag, default_owns, owns_conflicts
from .registry import DepthExceeded, live_zombies
from .settings import (AGENT, ANTI_JOB, BLOCKED, CONTRACT_SCHEMA, DONE, EXCLUSIVE, PARENT, PHASE, ROOT,
                       SUBSTATES, WORKER_STATES, ConfigError, _BUDGETS, worker_agents)
from .worker import Worker, WorkerHandle, default_pipeline

__all__ = ["Dispatcher", "WorkerHandle", "WorkerBoard", "Worker", "default_pipeline", "check_dag",
           "default_owns", "owns_conflicts", "worker_agents", "live_zombies", "CycleError", "DepthExceeded",
           "ConfigError", "CONTRACT_SCHEMA", "SUBSTATES", "AGENT", "PARENT", "DONE", "WORKER_STATES",
           "BLOCKED", "EXCLUSIVE", "ANTI_JOB", "PHASE", "ROOT", "escalation", "ledger",
           "_BUDGETS", "_overlap"]          # nội bộ, giữ cho test/code cũ (monkeypatch _BUDGETS)
