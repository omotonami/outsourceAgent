# Build Dispatcher (H2 — đề án 0002)

`src/orchestrator/dispatcher/` — Code thuần, phát `task_backlog` của B4 cho nhiều worker BUILD chạy song song.
Không LLM nào chọn thứ tự hay số worker. Import công khai: `from src.orchestrator.dispatcher import Dispatcher`.

| Module | Trách nhiệm |
|---|---|
| `settings.py` | hằng số, đọc `config/budgets.yaml → dispatcher` (thiếu → `ConfigError`), `worker_agents()` từ spec B4 |
| `dag.py` | `check_dag`, `owns` overlap, `default_owns`, `owns_conflicts` |
| `registry.py` | depth guard theo dự án + sổ zombie |
| `worker.py` | `WorkerHandle`, `default_pipeline` (c1 → c2 → c3) |
| `gitops.py` | worktree theo nhánh task cha, commit cục bộ |
| `scheduler.py` | vòng `run()`: chọn ready, spawn, chờ, KILL |
| `core.py` | class `Dispatcher`: substate, sự kiện, contract, budget, thread worker, kết quả |

## Luật
- **Thứ tự**: DAG `deps`. Id trùng, dep không tồn tại hoặc có vòng → `CycleError`, không spawn gì.
  Task *ready* khi mọi dep đã `await_merge` (hoặc `merged`).
- **Song song**: thread daemon (zombie không treo tiến trình lúc thoát), tối đa `dispatcher.max_parallel`
  (`config/budgets.yaml`; thiếu → `ConfigError`, không default trong code).
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
- **Depth 1, theo dự án**: worker và mọi thread do worker mở (đánh dấu dòng dõi khi `Thread.start`) tạo/chạy
  `Dispatcher` của bất kỳ dự án nào → `DepthExceeded`. Dự án còn worker đang chạy → không tạo/chạy thêm
  Dispatcher cho chính nó; **dự án khác chạy bình thường** (trước 2026-11-03 chặn cả tiến trình).
- **Trace**: `ASSIGN / SPAWN / RESULT / BLOCKED / KILL` append vào zone `trace_log` qua `events.emit`
  (writer `orchestrator` ở `specs/05_blackboard.yaml`) — nguồn cho Team feed (H5). `ASSIGN {agent, task_type,
  trigger}` ngay trước mỗi `SPAWN` (kể cả retry); task bị autonomy chặn không có ASSIGN.
- Kết thúc ghi `build_results.{all_checks_pass, qc_critical, loops}` để guard `build_ready` đọc. G3 giữ nguyên.

## Worker
`worker(contract, handle) -> {"ok": bool, "reason"?, "tokens_used"?, "tier"?, "qc_critical"?}`.
Mặc định `default_pipeline(user_id, ctx, sender)` (worker dùng `handle.board` = `WorkerBoard` ở
`src/blackboard/proxy.py`: chỉ đọc + ghi với danh tính agent con; không `sign`/`revoke`; không giữ tham chiếu board thật): c1_dev → c2_tester → c3_qc qua `runtime`/gateway (cần API key). Test dùng mock.

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
# res: spawned, done, blocked, pending, escalation, used_tokens, max_concurrency, worktrees, autonomy_denied
```
