# 2026-11-04 — Phòng 3D + realtime + token đã đốt

- Nhánh: `feat/v3-bot-team` · `0d87bd5` (370 test) → `0c2ed08` (396 test, `agent_lint --all` 14/14).
- Tài liệu: [`docs/room.md`](../room.md). Đợt trước: [2026-11-03 v3 bot team](2026-11-03_v3-bot-team.md).

## Thay đổi
| Mục | Đã đổi gì | Vì sao | File chính | Commit | Test |
|---|---|---|---|---|---|
| Phòng 3D (chỉ đọc) | `room_state.snapshot` (agent, trạng thái, task, event từ summary Team feed); `GET /api/room`, `GET /room`; tab Phòng 3D; Three.js 0.170.0 qua CDN, nhân vật low-poly tự thiết kế, demo mode, mobile + reduced-motion | Nhìn team agent làm việc như một căn phòng, không phải đọc log | `src/orchestrator/room_state.py`, `src/server/static/room.html`, `src/server/app.py` | `d54479b` | 379 |
| Realtime qua event store | SQLite WAL append-only dùng chung giữa process; scrub khoá raw thinking/prompt/contract; Blackboard ghi xuyên **sau** kiểm quyền + schema (mặc định tắt, bật bằng `config/runtime.yaml`); `Blackboard.load` dựng lại board chỉ đọc; SSE `/api/room/stream` (Last-Event-ID, heartbeat), replay `/api/room/frames`, `/api/projects`; LIVE / REPLAY / DEMO | Phòng 3D thấy dự án đang chạy ở process khác | `src/blackboard/eventstore.py`, `src/blackboard/store.py`, `src/orchestrator/room_stream.py`, `src/demo/live_team.py` | `0c2ed08` | 396 |
| Token đã đốt | Trace gateway ghi dòng `llm_usage` (chỉ số liệu); `usage.py` tổng hợp; đơn giá `config/pricing.yaml` (gỡ PRICE hard-code ở `server/demo.py`, luật 6); thẻ góc phải canvas | Thấy chi phí theo dự án/agent | `src/gateway/trace.py`, `src/orchestrator/usage.py`, `config/pricing.yaml` | `0c2ed08` | 396 |

## Quyết định của chủ repo (Daipc)
| Thời điểm | Quyết định |
|---|---|
| 2026-11-04 05:27 | "Duyệt luôn": (1) luật `confirmed_by` — bot không tự điền danh tính người (→ CLAUDE.md luật 10); (2) **duyệt card `e2_cos`**; (3) commit đề án 0002 vào repo; (4) push/PR **sau khi chủ repo test ok — CHƯA push**; (5) dọn nhánh trung gian — **đã xoá** `feat/h1..h4` |
| 05:46 | Yêu cầu ghi lịch sử thay đổi vào `docs/history/` |
| 05:49 | Làm Phòng 3D realtime |
| 06:00 | Thêm token đã đốt |

### Ghi chú về card `e2_cos` và `confirmed_by`
`e2_cos` được sinh ngày 2026-11-03 bằng `python -m src.factory.new --spec e2_cos.yaml --confirmed-by daipc --write`
(commit `fa4b226`). Giá trị `confirmed_by: daipc` khi đó **do bot điền**, không phải chủ repo gõ — vi phạm tinh
thần "người xác nhận". Chủ repo duyệt card qua hội thoại lúc 2026-11-04 05:27; ghi nhận bằng một dòng ghi chú
trong `specs/02_agents/e2_cos.md`, không đổi nội dung luật của card. Từ đợt
[quản trị](2026-11-04_governance-history.md), CLI Factory không còn nhận `--confirmed-by` khi `--write`.

## Hạn chế còn lại
- Event store chứa payload zone sản phẩm → **có thể có dữ liệu client** (PDPA). Đã scrub raw thinking/prompt,
  file ở `data/runtime/` (gitignore), nhưng xoá dữ liệu cũ là **thủ công** (`prune()`), chưa có lịch retention.
- Gate đang chờ **chưa hiện** trên Phòng 3D ở chế độ project (chỉ có ở demo).
- Sandbox của worker (WorkerBoard/proxy) chỉ **chống dùng nhầm**, không phải ranh giới bảo mật giữa code không tin cậy.
- Three.js tải từ CDN → Phòng 3D cần mạng.

## Việc còn mở
- Push/PR: chờ chủ repo test xong và báo ok.
- Lịch retention / xoá dữ liệu event store theo chính sách PDPA của dự án.
- Hiện gate đang chờ ở chế độ project trên Phòng 3D.
- Tuỳ chọn: vendor Three.js để chạy offline.
