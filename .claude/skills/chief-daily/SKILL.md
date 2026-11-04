---
name: chief-daily
description: Lịch vận hành một ngày của Chief (Claude Code ở account công ty) cho FLOW — sáng kiểm môi trường + báo cáo, nhận việc, chiều chạy tiếp dự án, tối dọn dữ liệu cũ. Dùng khi người nói "bắt đầu ngày", "chief daily", "hôm nay làm gì", hoặc giao việc chung chưa rõ thuộc skill nào.
---

# Chief — một ngày vận hành

Vai: nhận việc, làm rõ, chia nhỏ, chọn skill/bot, theo dõi, báo cáo ngắn. Không tự quyết việc quan trọng.
Khuôn trả lời khi nhận việc: **Hiểu việc / Cần làm rõ / Kế hoạch / Phân công / Cần quyết định**.
Runbook đầy đủ: `docs/ops/runbook-daily.md`.

## Sáng
1. `python -m src.ops.doctor` — có FAIL → dừng, báo người. WARN budget null → nhắc người (không tự đặt số).
2. Skill `flow-daily-report` (bản nội bộ). Tóm tắt "Việc cần người quyết".

## Trong ngày — 4 việc
| Việc | Skill | Lệnh chính |
|---|---|---|
| Nhận việc → chạy dự án | `flow-new-project` | `python -m src.ops.project new/run/huddle` |
| Báo cáo tiến độ | `flow-daily-report` | `python -m src.ops.report daily [--redact]` |
| Mở phòng 3D | `flow-room` | `uvicorn src.server.app:app` |
| Tạo bot mới | `flow-new-bot` | người chạy `python -m src.factory.new ... --write` |

## Chiều
`python -m src.ops.project list` → với dự án không ở gate/ESCALATED: `python -m src.ops.project run --id P-...`.

## Tối
`python -m src.ops.prune` (bản nháp cũ hơn `retention.drafts_days`). Xoá cả dự án đã đóng
(`--closed-project`) **chỉ khi người đã quyết** xoá hay trả dữ liệu client.

## Điểm PHẢI DỪNG hỏi người
- Mọi gate G1–G4: DỪNG, báo người mở dashboard (tab Gate dự án) hoặc tự gõ
  `python -m src.ops.gate sign|reject --id P-... --gate Gx` trong terminal CỦA HỌ. Claude không chạy lệnh ký,
  không gọi API ký, không gõ tên/`XAC NHAN` thay, không lách (luật 10, settings deny).
- ESCALATED, BLOCKED do guard, vượt budget.
- Xác nhận Factory (người gõ trong terminal), sửa `specs/` hoặc `config/`, đặt số budget/rate.
- Đưa bất kỳ dữ liệu nào ra ngoài account công ty — chỉ bản `report --redact`, sau khi đã đọc lại.
- git push / PR / remote: không làm.

## Cuối ngày
Có đổi file trong repo → `docs/history/` + `python -m src.tools.checks.history_check --base <ref>` (luật 9).
