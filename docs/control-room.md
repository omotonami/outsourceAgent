# Control Room — một trang `/` (đợt 2026-11-04)

Thay 3 cửa sổ (3D xem · chat Claude ra lệnh · dashboard theo dõi) bằng MỘT trang. Máy đã đăng nhập Claude Code
(lệnh `claude`); chạy thử không cần model: `MOCK=1` + `--mock`.
```
uvicorn src.server.app:app                     # terminal 1 → http://localhost:8000/   (MOCK=1 để chat CoS mock)
python -m src.ops.scheduler run [--mock]       # terminal 2 — chạy nền mọi dự án (Ctrl-C dừng; --once = một lượt)
```
- **Trái**: Phòng 3D "Tất cả dự án" (nhúng `/room?all=1&embed=1` — một nguồn room.html). Mỗi agent 1 nhân vật,
  trạng thái gộp; chip dự án góc phải dưới: `!` đỏ = ESCALATED, `⏳` = chờ gate. Bấm chip `!` hoặc nhân vật đang kẹt ở
  dự án ESCALATED → mở thẳng tab **Sự cố**. Dưới phòng: Trao đổi có nhãn dự án.
- **Phải** (realtime SSE `/api/room/all/stream`, không cần F5):
  | Tab | Làm gì |
  |---|---|
  | Chat CoS | Hỏi → `POST /api/cos/chat` → e2_cos trả 5 mục. **＋ Giao việc**: brief (client, nguồn, nội dung, file .md/.txt) → CoS soạn bản xem trước → NGƯỜI gõ tên + `XAC NHAN` → dự án mới (INTAKE), scheduler chạy tới G1. **＋ Giao việc phụ**: role + tiêu đề → người gõ tên + `XAC NHAN` |
  | Việc | Task theo dự án (backlog + việc phụ có note), nhân sự: chính ở P-x, phụ ở P-y |
  | Gate | Như tab Gate dự án cũ: highlights, zone, decision_records → gõ tên + `XAC NHAN` → Duyệt / Trả lại |
  | Sự cố | Dự án ESCALATED: lý do, phase lỗi, agent → Retry / Quay về phase / Huỷ (gõ tên + `XAC NHAN`) |
  | Token | Tổng + theo dự án + top agent (chi phí ước tính) |
- Mobile ≤760px: 3D trên, tab dưới. Không hiển thị raw thinking/prompt. Dashboard cũ: `/legacy`; phòng riêng: `/room`.

## Đa dự án — việc chính + việc phụ (`config/scheduler.yaml`)
Mỗi role ≤ `max_main_per_role` (1) việc chính cùng lúc + ≤ `max_side_per_role` việc phụ, chỉ khi role rảnh (không
dự án đang chạy nào cần role đó). `max_concurrent_calls` = tổng lượt chạy song song. Khoá file → không 2 scheduler.
Việc phụ = gọi agent CHỈ ĐỌC (`src/agents/side_task.py`), kết quả là note trong zone `side_tasks` — không ghi zone
sản phẩm. CLI: `python -m src.ops.project task add --id P-... --role b4_tech_lead --title "..." [--kind side|main]`,
`task list`. Scheduler KHÔNG ký gate, KHÔNG gỡ ESCALATED, KHÔNG chạy huddle (huddle vẫn gọi tay).

## Quyền
Chat CoS chỉ gọi `e2_cos.ask` (CosBoard read-only); không route CoS nào ký / gỡ / ghi zone sản phẩm. Rate-limit
`cos_chat.rate_limit_per_min`. Model không gọi được → "Claude Code chưa sẵn sàng — chạy `claude` và đăng nhập".
Claude Code không gọi các endpoint cần người (settings deny). Test: `tests/contract/test_control_room.py`,
`tests/contract/test_scheduler.py`.
