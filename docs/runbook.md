# Runbook

## Hàng tuần
- Xem tỉ lệ escalate theo agent (> 30% → xem lại tier mặc định hoặc skill).
- Xem chi phí theo phase so với `config/budgets.yaml`.
- Xem gate bị từ chối và lý do.

## Hàng tháng
- Chạy `eval/` trên holdout; duyệt skill chờ promote.

## Nâng model version
1. Sửa `config/models.lock` trên nhánh riêng.
2. Chạy eval suite đầy đủ.
3. Chỉ merge khi không metric nào xấu đi quá ngưỡng.

## Đóng dự án
- Đối chiếu estimate với timesheet, thêm dự án vào `eval/testsets/`.
- Xoá/trả dữ liệu client theo `blackboard/retention.yaml`.

## Hai vòng training
- Gate feedback: xem `eval/runs/gate_feedback.jsonl`; duyệt các đề xuất skill personal hàng tuần.
- Đóng dự án: chạy `close_project.close(...)`; kiểm `eval/runs/estimation_accuracy.jsonl`.
- Chi tiết: `docs/training-loops.md`.

## Lớp skill học được qua vận hành (vòng 3)
- Mọi lượt user sửa output → `revision.record(board, ...)` ghi vào Blackboard `revision_log`.
- Reflection của agent gọi `revision.reflection_context(board, agent)` để học ngay trong phiên.
- Định kỳ chạy `distiller.distill(revision_entries)` → pattern sửa lặp ≥3 lần thành `learned/PROPOSED.md`.
- Duyệt: chạy eval regression cho role; nếu tốt lên, đổi `PROPOSED.md` → `learned/SKILL.md` (lúc đó mới được nạp).
- Phân biệt kind='edit' (học) vs 'preference' (nhiễu, bỏ qua).
