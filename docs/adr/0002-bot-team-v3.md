# ADR 0002: Lớp "bot team" v3 trên lõi Code (Factory, Dispatcher, Huddle, Autonomy, CoS)

- Status: Accepted (2026-11-03) — đề án 0002 được chủ repo duyệt; triển khai 5 đợt + refactor trên nhánh `feat/v3-bot-team`.
- Đề án gốc: [`docs/proposals/0002-bot-team-v3.md`](../proposals/0002-bot-team-v3.md) (đã duyệt, commit vào repo 2026-11-04).
- Lịch sử triển khai + quyết định theo giờ: [`docs/history/2026-11-03_v3-bot-team.md`](../history/2026-11-03_v3-bot-team.md).

## Context
Lõi (state machine Code, guard từng transition, Blackboard phân quyền theo zone, gateway duy nhất ra model,
4 gate người ký) chặt nhưng cứng: thêm agent phải sửa tay nhiều chỗ, BUILD chạy tuần tự dù B4 đã ra DAG,
không có pha các agent phản biện nhau trước gate, autonomy cố định, người phải hiểu cả hệ mới dùng được.

## Decision
Thêm một lớp tổ chức bot lên trên lõi, **không đổi** state machine, guard `gates.py`, quyền Blackboard, gateway.
Nguyên tắc: chat thì sống, output thì cứng — mọi quyết định luồng, số liệu, quyền là Code.

1. **Agent Factory (H1)**: LLM chỉ nháp câu trả lời phỏng vấn; người xác nhận; Code sinh đủ bộ file trên nhánh
   `factory/<id>` (không ghi đè, rollback), `agent_lint` chặn agent thiếu chuẩn; agent mới L0, qua PR.
2. **Build Dispatcher (H2)**: Code phát `task_backlog` song song theo DAG; `owns` không giao; số worker, ttl,
   budget từ config; budget dùng chung sổ gateway; depth tối đa 1 (theo dự án); worker chỉ có board proxy;
   worktree mỗi task, task con tạo từ nhánh task cha; không bao giờ tự lên `merged` (G3 vẫn do người).
3. **Huddle (H3)**: Proposer → Critic → Decider cho PRESALES/DISCOVERY, trigger bằng Code, kết thúc bắt buộc
   bằng `decision_record`; chỉ append `huddle_log`/`decision_records`; gate chỉ thấy record, không thấy transcript.
   **Không nối vào state machine** — gọi tay.
4. **Autonomy Ladder (H4)**: mức theo cặp (agent, task_type), chỉ **L0–L2**; nâng = đề xuất + người duyệt,
   hạ = tự động khi có revision/gate edit/regression/incident. **L3 auto-gate bảo lưu**: không code path nào ký gate.
5. **Chief of Staff + Team feed (H5)**: `e2_cos` (sinh bằng Factory) chỉ đọc, trả lời theo 5 mục; chỉ được tạo
   `lead_brief` / `clarify_answers` khi người xác nhận (hash payload). Team feed dựng timeline chỉ từ summary.
   Figma adapter chỉ đọc.
6. Config một nguồn: runtime đọc thẳng `specs/03_routing.yaml`, `specs/05_blackboard.yaml` (bỏ bản sao trong `src/`);
   writer `orchestrator` cho `trace_log`; `owns` là field output B4.
7. Refactor sau 5 đợt (không đổi hành vi): loader config một chỗ (`src/common/config.py`, thiếu → `ConfigError`),
   một cơ chế xác nhận người (`src/governance/confirm.py`), một module board proxy (`src/blackboard/proxy.py`),
   event log chung (`src/orchestrator/events.py`), tách file > 350 dòng (dispatcher → package, scaffold →
   templates/yaml_edit, server → demo). Kèm 4 sửa lỗi đã biết: depth guard theo dự án, phát ASSIGN,
   lỗi ghi review sau khi ký gate không làm request lỗi, proxy không lộ board thật.

## Options bị loại
- Cho agent chat tự do trong BUILD/SECURITY; để LLM chọn luồng, số worker hay model — mất tính kiểm chứng.
- Agent do bot tạo chạy ngay không qua người; auto-merge ở rủi ro cao (L3) — bảo lưu tới khi có dữ liệu và chủ repo duyệt.
- Giữ bản sao config trong `src/` — đã gây lệch; chọn đọc thẳng `specs/`.

## Consequences
- Thêm agent = 1 phiên phỏng vấn + PR; mọi agent mới đều qua `agent_lint`.
- BUILD song song an toàn hơn nhờ `owns` + worktree, nhưng phụ thuộc git cục bộ; tool ngoài khác (Jira, scanner) vẫn stub.
- Danh sách "không phải người" dùng chung → mọi kênh xác nhận chặt như nhau (vd `gate_ui` không phải người xác nhận;
  autonomy.promote từ chối mọi danh tính agent).
- Chưa có số liệu thật để nâng autonomy; L3 và nối Huddle vào state machine là quyết định mở cho chủ repo.
