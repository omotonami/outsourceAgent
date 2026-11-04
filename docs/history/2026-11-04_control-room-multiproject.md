# 2026-11-04 — Control Room một trang + đa dự án "việc chính + việc phụ"

- Nhánh: `feat/control-room` · từ `a4d041e` (545 test) → commit của đợt (prefix `feat(control-room)`).
- Giao việc: Chief of Staff của chủ repo (Daipc) giao bot "Điều khiển".

## Vấn đề từ chạy thật ở công ty (chủ repo báo, nguyên văn ý chính)
| Mã | Hiện tượng |
|---|---|
| A | Phòng 3D treo khi dự án ESCALATED, không gỡ được trên giao diện. |
| B | Không chạy nhiều dự án / task giao thêm; team thật làm việc chính và còn điều tra task của dự án khác. |
| C | Khó dùng: 3 cửa sổ (3D để xem, chat Claude để ra lệnh, dashboard để theo dõi). |

## Quyết định của chủ repo
- 2026-11-04 10:2x (qua Chief of Staff): Control Room 1 trang, chat với CoS TRONG APP (agent e2_cos qua gateway); đa dự
  án theo mô hình "việc chính + việc phụ", có trần trong config. Được duyệt: thêm zone `side_tasks` vào
  `specs/05_blackboard.yaml`; 1 dòng CLAUDE.md (mục Vận hành hằng ngày) nhắc `src.ops.escalation` và `src.ops.scheduler`;
  `config/runtime.yaml agent_runtime.schema_retries` 1 → 2 cho khớp card "retry ≤ 2".
- 2026-11-04 10:54: bỏ ANTHROPIC_API_KEY — model chạy qua Claude Code CLI đã đăng nhập (bot khác đổi
  `src/gateway/llm_client.py` ở nhánh `feat/superpowers`; đợt này KHÔNG sửa llm_client). UI/docs không nhắc API key;
  model không gọi được → "Claude Code chưa sẵn sàng — chạy `claude` và đăng nhập".

## Ranh giới (đề bài)
Không nới gate/guard; không thêm đường máy ký/gỡ; không L3; không auto-ký; huddle vẫn gọi tay; không sửa agents C1/C2/C3
hay knowledge/; không push/PR/remote; không tự điền danh tính người.

## Mục 1 — Scheduler đa dự án (lỗi B)
| Đã đổi gì | File chính |
|---|---|
| `python -m src.ops.scheduler run [--once] [--mock] [--db]` / `status`: mỗi vòng duyệt mọi project trong event store, chạy tiếp tới gate/ESCALATED/BLOCKED bằng pipeline (mỗi project 1 thread); bỏ qua RELEASED/ABANDONED/ESCALATED, gate chưa ký, BLOCKED khi store chưa đổi (không spam). Khoá file `scheduler.lock` cạnh event store (flock) → process thứ 2 rc 3. Không có đường ký gate / gỡ escalate / chạy huddle | `src/ops/scheduler.py`, `config/scheduler.yaml` |
| `Slots`: mỗi role ≤ `max_main_per_role` việc chính + ≤ `max_side_per_role` việc phụ; việc phụ chỉ khi role rảnh (không việc chính đang chạy/chờ, không dự án đang chạy ở phase cần role — `STATE_ROLES`); `max_concurrent_calls` tổng | `src/ops/scheduler.py` |
| Pipeline: `Runner(slots=)` — `slots.hold((agent,), pid, "main")` bao mỗi lần gọi agent (RESULT/BLOCKED ghi trước khi nhả role); BUILD giữ C1–C3 trong lượt Dispatcher. Không truyền slots → như cũ. ASSIGN mang `project` + `kind` (pipeline, Dispatcher) | `src/ops/pipeline.py`, `src/orchestrator/dispatcher/scheduler.py` |
| `events.emit(board, kind, /, ...)` và `Dispatcher._event(kind, task_id, /, ...)` positional-only để field `kind` dùng được | `src/orchestrator/events.py`, `src/orchestrator/dispatcher/core.py` |
| Task giao thêm: zone `side_tasks` (append-only, writer orchestrator — duyệt 10:2x) queued/started/done/failed + note; `project task add|list`. Việc phụ = agent CHỈ ĐỌC (`src/agents/side_task.py`, ReadOnlyBoard, qua gateway) trả `{summary, findings, risks, recommendations}` gắn nhãn; KHÔNG ghi zone sản phẩm; lỗi → failed + RESULT ok=False (không BLOCKED role). Task `started` cũ (process chết) chạy lại ở vòng đầu | `specs/05_blackboard.yaml`, `src/ops/side_tasks.py`, `src/agents/side_task.py`, `src/ops/project.py` |
| Mock: e2_cos (khuôn 5 mục) + việc phụ | `src/ops/mock.py` |

## Mục 2 — Phòng 3D đa dự án (lỗi A, B)
| Đã đổi gì | File chính |
|---|---|
| `room_state`: agent có `kind` main/side; `side_tasks(board)` (nguồn đọc duy nhất) | `src/orchestrator/room_state.py` |
| `MultiFeed`: mọi project → 1 view (mỗi agent 1 nhân vật, status gộp blocked > working > waiting_gate > idle, `work`/`main`/`side`), task theo dự án (+ việc phụ), event nhãn dự án (id `P|…`, task `P:T1`), usage gộp + `by_project`, `projects` (escalated/gate), seq toàn cục → SSE nối lại. `GET /api/room/all`, `/api/room/all/stream` | `src/orchestrator/room_stream.py`, `src/server/control.py` |
| room.html: mặc định "Tất cả dự án"; chip dự án (`!` ESCALATED / `⏳` gate), bấm `!` hoặc nhân vật kẹt ở dự án ESCALATED → tab Sự cố (postMessage khi nhúng); Trao đổi nhãn dự án; Task nhóm theo dự án; token theo dự án; `?embed=1` | `src/server/static/room.html` |

## Mục 3 — Control Room `/` (lỗi A, C)
| Đã đổi gì | File chính |
|---|---|
| `/` = Control Room: trái iframe `/room?all=1&embed=1` (một nguồn room.html), phải tab Chat CoS · Việc · Gate · Sự cố · Token, realtime SSE, mobile ≤760px 3D trên/tab dưới. Dashboard cũ → `/legacy` | `src/server/static/control.html`, `src/server/app.py` |
| `POST /api/cos/chat` → `e2_cos.ask` (MOCK=1 → mock); `POST /api/cos/brief` (bản xem trước lead_brief + 5 mục, file .md/.txt); `POST /api/cos/brief/confirm` (người gõ tên + XAC NHAN → `e2_cos.confirm/submit`, thiếu → 400, sửa bản xem trước → 409); `GET/POST /api/projects/{id}/side-tasks` (người XAC NHAN); rate-limit `cos_chat.rate_limit_per_min`; model lỗi → 503 NOT_READY thông báo chung | `src/server/control.py`, `config/scheduler.yaml` |
| Sự cố: nút Retry / Quay về phase / Huỷ gọi API sẵn có `POST /api/projects/{id}/escalation/{action}` | `src/server/static/control.html` |
| Giao việc phụ qua dashboard đi thẳng `side_tasks.add` (người XAC NHAN), KHÔNG qua `e2_cos.submit` — spec e2_cos chỉ cho 2 loại yêu cầu (lead_brief, clarify_answers), không sửa Forbidden | `src/server/control.py` |
| `.claude/settings.json`: deny curl/wget/http tới `api/cos/brief/confirm`, `api/projects/*/side-tasks`; allow `project task list`, `scheduler status` | `.claude/settings.json` |
| `eventstore.register(pid, None)` = project ảo (chat CoS không gắn dự án không ghi usage vào store) | `src/blackboard/eventstore.py` |

## Mục 4 — Config / docs
- `config/runtime.yaml schema_retries: 2`; test cũ cập nhật đọc số từ config (`test_runtime_extract.py`, `test_dispatcher.py`).
- CLAUDE.md (1 mục Vận hành hằng ngày), `docs/control-room.md` (mới), START_HERE (mở 1 trang `/` là đủ; bỏ API key),
  `docs/ops/runbook-daily.md`, `docs/ops/chief-prompt.md`, `docs/room.md`, `.claude/skills/flow-room/SKILL.md`.

Test mới: `tests/contract/test_scheduler.py` (13: 3 dự án song song tới G1 đúng trần main/role và lượt chạy, việc phụ chờ
role rảnh + chỉ ghi note, trần side, ưu tiên việc chính chờ, board chỉ đọc, việc phụ lỗi không BLOCKED, BLOCKED không chạy
lại, ESCALATED/gate bỏ qua + không có đường ký, task started cũ, khoá 2 scheduler, CLI task, config) ·
`tests/contract/test_control_room.py` (15: trang `/` + `/legacy` + room, chat 5 mục không ghi, chat không ký/gỡ/ghi
sản phẩm, rate-limit, brief → XAC NHAN (thiếu → 400, tên máy → 400, sửa → 409), lỗi brief, việc phụ cần XAC NHAN, gỡ
ESCALATED từ UI rồi scheduler chạy tiếp, abandon, room all gộp, merge main/side, MultiFeed poll + SSE, settings deny,
agent kind, model không sẵn sàng → thông báo Claude Code).
Playwright (uvicorn MOCK=1 + `scheduler run --mock`, 3 dự án: 2 ở G1, 1 ESCALATED, 2 việc phụ): `/home/claude/control-desktop.png`,
`/home/claude/control-mobile.png` (iPhone 13), không lỗi console; bấm chip `!` mở tab Sự cố.

Số test sau đợt: **573**.

## Việc còn mở
- `src/ops/doctor.py` vẫn kiểm `ANTHROPIC_API_KEY` (WARN) và `.claude/skills/flow-new-project` còn nhắc key — đổi cùng
  đợt Claude Code CLI của nhánh `feat/superpowers`.
- Chat CoS không gắn dự án: token không vào event store (không có project); gắn dự án thì vào `llm_usage` của dự án đó.
- BUILD giữ cả C1–C3 cho một dự án trong suốt lượt Dispatcher (đơn giản, an toàn); chia nhỏ theo task nếu cần.
- Việc phụ kind=main không chặn cứng việc chính của pipeline (chỉ chặn việc phụ cùng role).
- ~~Test cũ `test_dispatcher.py::test_invalid_contract_not_spawned` chập chờn~~ → đã sửa ở mục Gộp `fix/field-events` dưới.
- Server localhost không đăng nhập: ai mở được trang là gõ được tên (như gate) — rào cấu hình + XAC NHAN, không phải xác thực.

## Gộp `fix/field-events` (review PR #2) vào `feat/control-room` (bot "Nối")
`git merge origin/fix/field-events` (5f08112: a4d041e + 7 commit sửa review) vào 9783640.

| Xung đột / chỗ ghép | Cách giải (giữ ý đồ cả hai) |
|---|---|
| `config/runtime.yaml` | `schema_retries: 2` (Control Room, chủ repo duyệt) + khoá mới của review: `extract_max_chars`, `extract_max_candidates`, `dashboard.allowed_hosts`; `eventstore.assign_stale_s` (tự gộp) |
| `src/orchestrator/room_state.py` | `snapshot`: `step_status(state, now, window_s=assign_stale_s)` (review: bỏ working kẹt khi gate/terminal/ASSIGN quá hạn) + `step_kinds` (main/side) + tham số `meta` đa dự án |
| `docs/history/README.md` | giữ dòng field-fixes bản review (→ 676) + dòng Control Room |
| `.claude/settings.json` (tự gộp) | hợp nhất: allow pytest chỉ `pytest -q tests/`; ask `tests/**`; deny gồm cả `git diff --no-index*` (review) và `api/cos/brief/confirm`, `side-tasks` (Control Room) — giữ mức chặt hơn |
| `src/server/app.py` (tự gộp) | chốt ghi là middleware cấp app → router `control.py` (`/api/cos/chat`, `/api/cos/brief`, `/api/cos/brief/confirm`, `/api/projects/{pid}/side-tasks`) cũng qua kiểm Host/Origin (403) + Content-Type JSON (415); `control.html` gửi mọi POST qua helper `post()` JSON |

Sửa sau gộp (xung đột ngữ nghĩa):
- `tests/contract/test_control_room.py`: TestClient `base_url="http://localhost"` (như hạ tầng review); body không phải JSON
  gửi kèm `Content-Type: application/json` để vẫn kiểm 400. Test mới: 4 endpoint ghi Control Room × 5 ca Host/Origin/
  Content-Type sai → 403/415 và không ghi event; UI POST JSON.
- `tests/contract/test_runtime_extract_review.py`: số output sai = `1 + runtime.schema_retries()` (review viết khi =1).
- Test chập chờn `test_dispatcher.py::test_invalid_contract_not_spawned` — nguyên nhân: sổ zombie là cấp module;
  `test_ttl_kill_blocks_task` cùng pid `p-h2`, cùng task id `A` để lại zombie "A" khi thread chưa kịp thoát (máy tải
  nặng) → `_mark_ready` loại "A" vì bận, nhưng "A" không vào `waiting` nên `run()` thoát luôn, "A" ở pending thay vì
  blocked. Tái hiện chắc chắn bằng worker ngủ 0,3 s sau cancel. Sửa test (không đụng dispatcher): pid riêng cho hai
  test + test ttl chờ zombie của nó dừng (`_wait_no_zombies`). Hành vi scheduler (task trùng id với zombie bị bỏ qua
  im lặng, không chờ/escalate) ghi vào việc còn mở để chủ repo quyết.

Kiểm: `pytest -q` **725** pass (676 + 28 Control Room + 21 mới), chạy cả bộ 10 lần liền xanh; `test_dispatcher.py`
10 lần liền xanh; `agent_lint --all` 14/14; `history_check --base origin/main` PASS.
Playwright (MOCK=1 uvicorn + `scheduler run --mock`, store `/tmp`, 3 dự án + 1 ESCALATED thêm): chat CoS trả 5 mục;
Giao việc → XAC NHAN tạo dự án; tab Sự cố retry → 200, scheduler chạy tiếp tới G1; 0 lỗi console, 0 phản hồi 403/415.
Ảnh: `/home/claude/control-desktop.png`, `/home/claude/control-mobile.png`.

Việc còn mở (chủ repo quyết):
- Dispatcher: task ready trùng id với zombie còn sống của cùng dự án bị bỏ qua im lặng (không vào `waiting` → không
  `_wait_zombies`/escalation). Đề xuất: tính là waiting. Chưa sửa vì ngoài phạm vi gộp.
