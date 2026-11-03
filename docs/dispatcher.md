# Build Dispatcher (H2 — đề án 0002)

`src/orchestrator/dispatcher.py` — Code thuần, phát `task_backlog` của B4 cho nhiều worker BUILD chạy song song.
Không LLM nào chọn thứ tự hay số worker.

## Luật
- **Thứ tự**: DAG `deps`. Id trùng, dep không tồn tại hoặc có vòng → `CycleError`, không spawn gì.
  Task *ready* khi mọi dep đã `await_merge` (hoặc `merged`).
- **Song song**: `ThreadPoolExecutor`, tối đa `dispatcher.max_parallel` (`config/budgets.yaml`, mặc định 3).
- **Spawn Contract**: mỗi worker nhận contract hợp lệ theo `specs/04_schemas/spawn_contract.json`
  (`trigger`: `fan-out` lần đầu, `retry-fresh` khi chạy lại).
- **owns**: owns của các worker đang chạy không giao nhau (so cả tiền tố thư mục); giao → task chờ.
  `owns` (path/glob) là field của `task_backlog` trong output B4 (`specs/04_schemas/b4_tech_lead.out.json`);
  Dispatcher đọc thẳng từ task. Thiếu → `["*"]` = độc quyền → chạy tuần tự. Glob so thận trọng (phần tĩnh).
  `owns_conflicts(tasks)` kiểm Done-when của B4: cặp task song song có owns giao nhau.
- **Budget**: mỗi spawn trừ `worker_tokens` khỏi `per_project_tokens.build`; báo `tokens_used` thì trừ theo thực tế.
  Hết budget → ngừng spawn, `escalation.should_escalate(over_budget=True)`, ghi `risk_register`.
- **Substate** (zone `task_backlog`, writer `orchestrator`): `ready → in_dev → testing → qc → await_merge`.
  Worker chỉ được báo `in_dev/testing/qc`. **Không bao giờ `merged`** — chỉ sau G3 do người ký.
- **Retry**: tối đa `max_build_loops` lần thử; hết vòng (hoặc Opus vẫn fail) → `blocked`, task phụ thuộc cũng `blocked`.
- **TTL**: quá `worker_ttl_s` → `KILL` (set `handle.cancelled`), task `blocked`, owns giữ tới khi thread dừng.
- **Depth 1**: tạo/gọi `Dispatcher` từ thread worker → `DepthExceeded`.
- **Trace**: `SPAWN / RESULT / BLOCKED / KILL` append vào zone `trace_log` (đã thêm writer `orchestrator`
  ở `specs/05_blackboard.yaml` + `src/blackboard/permissions.yaml`) — nguồn cho Team feed (H5).
- Kết thúc ghi `build_results.{all_checks_pass, qc_critical, loops}` để guard `build_ready` đọc. G3 giữ nguyên.

## Worker
`worker(contract, handle) -> {"ok": bool, "reason"?, "tokens_used"?, "tier"?, "qc_critical"?}`.
Mặc định `default_pipeline(user_id, ctx, sender)` (worker dùng `handle.board` = `WorkerBoard`): c1_dev → c2_tester → c3_qc qua `runtime`/gateway (cần API key). Test dùng mock.

## Git (`src/tools/integrations/repo.py`)
Truyền `repo=` → mỗi task một worktree `<repo>.worktrees/<id>` trên nhánh `task/<id>`.
- Không dep → nhánh tạo từ `HEAD`. Một dep → tạo từ **nhánh task cha** `task/<dep>`.
- Nhiều dep → nhánh tích hợp tạm `integ/<id>` (từ `task/<dep0>`, merge lần lượt các `task/<depN>`),
  dùng làm base rồi xoá. Xung đột merge → task `blocked` với lý do `xung đột merge nhánh deps [...]: [file...]`.
- Task đạt `await_merge` → Dispatcher commit cục bộ thay đổi trong worktree lên `task/<id>` (để task con thấy).
  Dep-ready vẫn = `await_merge`; không ai tự lên `merged`.
Adapter chỉ cho `init/clone/worktree/branch/status/diff/add/commit/merge (cục bộ)/...`; `push` và lệnh remote bị từ chối.

## Dùng
```python
from src.orchestrator.dispatcher import Dispatcher
res = Dispatcher(board, repo="/path/repo", owns_of=lambda t: t.get("owns", ["*"])).run()
# res: spawned, done, blocked, pending, escalation, used_tokens, max_concurrency, worktrees
```
