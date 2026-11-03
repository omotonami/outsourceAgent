---
name: flow-new-project
description: Nhận một việc/brief mới và chạy dự án outsourcing qua FLOW (Orchestrator) tới điểm cần người. Dùng khi chủ repo giao brief/lead mới, hoặc bảo "chạy tiếp dự án P-...", "huddle presales/discovery". Không dùng để ký gate.
---

# Nhận việc → chạy dự án

Mọi lệnh chạy ở gốc repo. Dữ liệu dự án là dữ liệu CÔNG TY: ở lại máy/account công ty.

## A. Dự án mới
1. Làm rõ trước (hỏi người nếu thiếu): `client`, `source`, `received_at` là bắt buộc. Không tự bịa.
2. Viết brief vào `data/runtime/briefs/<tên>.yaml` (đã gitignore, KHÔNG để trong git):
   ```yaml
   client: <tên client>
   source: email|call|rfp
   received_at: YYYY-MM-DD
   domain: fintech|retail|...
   summary: <tóm tắt yêu cầu>
   ```
   (Hoặc `.md` có front matter YAML các khoá trên, thân bài = mô tả.)
3. `python -m src.ops.project new --brief data/runtime/briefs/<tên>.yaml` → ghi lại mã `P-...` in ra.
   Mã mặc định không chứa tên client — giữ như vậy (để bản `--redact` dùng được).
4. `python -m src.ops.project run --id P-...` (chưa có `ANTHROPIC_API_KEY` hoặc chạy thử: thêm `--mock`).

## B. Chạy tiếp dự án đang có
`python -m src.ops.project status --id P-...` rồi `python -m src.ops.project run --id P-...`.
`run` dựng lại state từ event store, chạy agent của phase hiện tại (BUILD qua Dispatcher) và DỪNG ở:
- `AWAIT_GATE (Gx)` → **DỪNG. Báo người** gate nào, ai ký (G1 Sales Lead · G2 Client+PM · G3 Tech Lead ·
  G4 Tech Lead+bảo mật), mở dashboard (skill `flow-room`). **Claude không ký, không tìm cách ký thay** —
  không có lệnh ký trong CLI và không được ghi `gate_signatures` / `sign` bằng bất kỳ đường nào (luật 10).
  Lưu ý hiện tại: gate_ui chỉ ký board demo; ký gate cho project event store chưa có → báo người, không tự làm.
- `ESCALATED` → DỪNG, báo lý do + risk; người quyết. Không tự gỡ.
- `BLOCKED` → DỪNG, báo lý do (guard từ chối / task blocked / thiếu key / lỗi mạng). Không nới guard (luật 4).

## C. Huddle (chỉ khi người yêu cầu)
`python -m src.ops.project huddle --id P-... --phase presales|discovery [--question "..."] [--mock]`
Kết quả là RECOMMENDATION cho gate; người quyết. Không tự chạy huddle trong `run`.

## Báo cáo lại cho người (khuôn ngắn)
- Đã xong: lệnh nào, state hiện tại
- Kết quả chính: agent đã chạy, token
- Cần người quyết/duyệt: gate / ESCALATED / BLOCKED (từ khối "CẦN NGƯỜI LÀM")

## Cuối việc
Nếu có thay đổi file trong repo (không tính `data/runtime/`): ghi `docs/history/` (file đợt + dòng mục lục)
rồi `python -m src.tools.checks.history_check --base <ref>` (luật 9). Không push.
