# 2026-11-04 — Quản trị: lịch sử thay đổi + xác nhận người

- Nhánh: `feat/v3-bot-team` · từ `0c2ed08` (396 test). Commit của đợt: `git log 0c2ed08..` (prefix `docs(history)`,
  `docs(governance)`, `feat(factory)`, `feat(checks)`).
- Theo quyết định chủ repo 2026-11-04 05:27 (luật `confirmed_by`, duyệt card e2_cos, commit đề án) và 05:46
  (ghi `docs/history/`). Chi tiết quyết định: [2026-11-04 Phòng 3D + realtime](2026-11-04_room-3d-realtime.md).

## Thay đổi
| Mục | Đã đổi gì | Vì sao | File chính | Test sau mục |
|---|---|---|---|---|
| Lịch sử thay đổi | `docs/history/` (quy ước + mục lục + 3 file đợt); commit đề án 0002, link từ ADR 0002 | Truy được ai quyết gì, khi nào, ở commit nào | `docs/history/*`, `docs/proposals/0002-bot-team-v3.md`, `docs/adr/0002-bot-team-v3.md` | 396 |
| Luật 9–10 | CLAUDE.md thêm luật 9 (mọi thay đổi ghi `docs/history/` cùng commit/PR) và 10 (bot không tự điền `confirmed_by`/`approved_by`/`reviewer`); 8 luật cũ giữ nguyên | Quyết định 05:27 + 05:46 | `CLAUDE.md` | 396 |
| Ghi nhận duyệt `e2_cos` | Thêm 1 dòng ghi chú vào card; không đổi nội dung luật | Card do bot điền `confirmed_by`; người duyệt sau qua hội thoại | `specs/02_agents/e2_cos.md` | 396 |
| Enforce luật 10 | CLI `src.factory.new --write`: không nhận `--confirmed-by`; bắt TTY (`sys.stdin.isatty()`), hiện tóm tắt card + hash, người gõ tên + gõ lại `XAC NHAN`; không TTY → từ chối. Bản ghi xác nhận có sẵn trong file spec cũng phải xác nhận lại. Dry-run giữ như cũ (không ghi gì). API Python `interview.confirm()` giữ cho test, docstring ghi rõ | Không còn đường để bot ghi danh tính người qua tham số | `src/factory/new.py`, `src/factory/interview.py`, `tests/contract/test_factory.py`, `docs/factory.md` | 405 |
| Enforce luật 9 | `python -m src.tools.checks.history_check --base <ref>`: fail nếu diff có `src/` `specs/` `config/` mà không có file `docs/history/` đổi, hoặc file đợt thiếu dòng mục lục | Kiểm bằng Code, chạy được trong CI | `src/tools/checks/history_check.py`, `tests/unit/test_history_check.py`, `README.md` | 411 |
| README / HANDOFF | Số test mới, link `docs/history/`, lệnh `history_check` | | `README.md`, `docs/HANDOFF.md` | 411 |

Số test sau đợt: **411** (396 + 15 mới: 9 CLI xác nhận tương tác, 6 `history_check`). `agent_lint --all` 14/14.
`history_check --base b7731d3`: PASS.

## Nhật ký commit của đợt
1. `docs(history)` — tạo docs/history, commit đề án 0002, link ADR.
2. `docs(governance)` — CLAUDE.md luật 9–10, ghi chú duyệt card e2_cos, README "10 luật".
3. `feat(factory)` — CLI `new --write` bắt xác nhận tương tác (TTY + tên + `XAC NHAN`), bỏ `--confirmed-by` khi ghi; `interview.preview_spec`; 9 test mới → 405.
4. `feat(checks)` — `src/tools/checks/history_check.py` + 6 test (repo git tạm) → 411; README/HANDOFF số test, link docs/history, lệnh chạy.

## Việc còn mở
- Gắn `history_check --base <nhánh đích>` vào CI khi có remote/CI (hiện chưa push).
- `approved_by` / `reviewer` ở kênh khác (autonomy.promote, gate_ui) đã chặn danh tính máy nhưng vẫn nhận
  chuỗi từ API — muốn chặt như CLI Factory cần xác thực người ở tầng server (đăng nhập), ngoài phạm vi đợt này.
- `history_check` chỉ kiểm có/không file history đổi, không kiểm nội dung khớp thay đổi — vẫn cần người review.
