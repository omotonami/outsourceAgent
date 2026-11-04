# Lịch sử thay đổi

Mọi thay đổi (code, `specs/`, `config/`, docs) phải được ghi ở đây **trong cùng commit/PR** trước khi
báo xong (CLAUDE.md luật 9). Kiểm bằng Code:

```bash
python -m src.tools.checks.history_check --base <ref>    # vd --base b7731d3 hoặc --base main
```
Fail nếu diff so với `<ref>` có file trong `src/` `specs/` `config/` mà `docs/history/` không đổi file nào,
hoặc có file đợt chưa có dòng trong mục lục dưới đây.

## Quy ước
- Mỗi **đợt** thay đổi = một file `YYYY-MM-DD_<slug>.md` (ngày bắt đầu đợt, slug chữ thường, nối `-`).
- Nội dung: bối cảnh · quyết định của chủ repo (ngày giờ, nguyên văn ý chính) · từng mục
  *đã đổi gì · vì sao · file chính · commit · số test sau mục* · kết thúc bằng mục **Việc còn mở**.
- Quyết định của người chỉ ghi khi người đã nói ra (hội thoại, gate_ui, terminal do chính người gõ).
  Bot không tự điền danh tính người (CLAUDE.md luật 10).
- Đợt đang làm dở: thêm vào file của đợt đó; đợt mới: tạo file mới + thêm 1 dòng mục lục.
- Số test = số test pytest thu thập được tại commit đó (`pytest --collect-only -q`), tất cả xanh.

## Mục lục
| Ngày | Đợt | Tóm tắt | Commit range |
|---|---|---|---|
| 2026-11-03 | [Biên bản review](2026-11-03_review-records.md) | Biên bản 2 vòng review của bot Soi: vòng 1 FAIL, vòng 2 PASS có điều kiện | `b7731d3..0820ef4` |
| 2026-11-03 | [v3 bot team](2026-11-03_v3-bot-team.md) | Đề án 0002: H1 Factory · H2 Dispatcher · H3 Huddle · H4 Autonomy L0–L2 · H5 CoS/Team feed/Figma + 2 vòng review + refactor (28 → 370 test) | `b7731d3..0d87bd5` |
| 2026-11-04 | [Phòng 3D + realtime](2026-11-04_room-3d-realtime.md) | Phòng 3D chỉ đọc, event store SQLite + SSE, token đã đốt; quyết định 05:27 (370 → 396 test) | `0d87bd5..0c2ed08` |
| 2026-11-04 | [Quản trị: lịch sử + xác nhận người](2026-11-04_governance-history.md) | docs/history, luật 9–10 trong CLAUDE.md, `history_check`, CLI Factory bắt xác nhận tương tác | `0c2ed08..64f7259` |
| 2026-11-04 | [Bộ vận hành công ty](2026-11-04_company-ops-kit.md) | CLI `src/ops` (project/report/prune/doctor), `.claude/` skills + settings, START_HERE, runbook, chief-prompt (411 → 434 test); vòng 2: cài từ bundle, history_check báo rõ khi không có .git; vòng 3: người ký gate dự án thật qua dashboard + terminal TTY (434 → 481) | `64f7259..` (nhánh `feat/v3-bot-team`) |
| 2026-11-04 | [Sửa lỗi chạy thực tế](2026-11-04_field-fixes.md) | Lỗi A1 SCHEMA_FAIL (parse JSON lẫn văn bản, schema vào prompt, spec↔schema), người gỡ ESCALATED, pipeline phát ASSIGN/RESULT theo agent (481 → 545 test) | `59785eb..` (nhánh `feat/v3-bot-team`) |
| 2026-11-04 | [Control Room + đa dự án](2026-11-04_control-room-multiproject.md) | Trang `/` một cửa (3D tất cả dự án + Chat CoS · Việc · Gate · Sự cố · Token), scheduler đa dự án việc chính + việc phụ, gỡ ESCALATED trên UI (545 → 573 test) | `a4d041e..` (nhánh `feat/control-room`) |
