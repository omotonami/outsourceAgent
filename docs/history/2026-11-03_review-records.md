# Biên bản review — bot Soi (đợt v3)

Tóm tắt biên bản thật của 2 vòng review (lưu lại từ hội thoại với Chief of Staff, 2026-11-03/04).
Reviewer chỉ đọc, không sửa code; mọi kịch bản "đã chạy" được chứng minh bằng script ngoài repo.

## Vòng 1 — H1 Factory + H2 Dispatcher (`b7731d3..d09a2b5`)
**Verdict: FAIL** · 64 test pass · lint 9/12.

| Mức | Vấn đề | Chứng minh | Sửa ở |
|---|---|---|---|
| Critical | `repo.py`: chèn option git qua url/base (fsmonitor, `--output`) → chạy lệnh tùy ý | Đã chạy | Vá #1 |
| High | Task con tạo worktree từ HEAD, không thấy code task cha | Lập luận | Quyết định 19:16 + Nối (a) |
| High | Depth guard `threading.local` lách bằng thread mới | Đã chạy | Vá #2 |
| High | Zombie worker giữ owns/worktree sau `run()` | Đã chạy | Vá #3 |
| High | Budget kiểm-rồi-gọi không lock; sổ dispatcher tách gateway | Đã chạy | Vá #4 |
| High | Nháp LLM lọt `build_spec` không qua `confirm`; khoá lạ vào prompt | Đã chạy | Vá #5 |
| Medium | Lint lách test; không so 2 bản config; writer `trace_log` chưa duyệt; `factory_interviewer` ngoài guard; `--write` ghi thẳng nhánh hiện tại; worktree không dọn | Đã chạy / lập luận | Vá #6–10, quyết định 19:16 |
| Low | TOCTOU cancel; runtime không trả tier khi lỗi; `sign()` không kiểm quyền; số hard-code; test chấp nhận rc 1 | Đã chạy / lập luận | Vá #11–14, #7 |

## Vòng 2 — review cuối toàn v3 (`b7731d3..0820ef4`)
**Verdict: PASS có điều kiện** · 314 test · lint 14/14 · dry_run tới RELEASED. Không có Critical.
Điều kiện trước merge: (1) clarify.resolve, (2) bỏ `handle._d`, (3) chuẩn hoá danh tính.

| Mức | Vấn đề | Chứng minh | Sửa ở |
|---|---|---|---|
| High | `clarify.resolve` fallback ghi `board._data` → lách quyền + schema | Đã chạy | `bce05d4` |
| Medium | `WorkerHandle._d` lộ board thật → worker ký được G3 | Đã chạy | `954a6b1` |
| Medium | Denylist phân biệt hoa/thường; `via` tự khai | Đã chạy | `e820f15` |
| Medium | Huddle ghi 500 ký tự output thô LLM vào log/feed | Đã chạy | `77c97e7` |
| Medium | `git diff` chạy `diff.external`/textconv/filter của repo | Đã chạy | `fdaa1fe` |
| Medium | Hook `Thread.start` gắn cờ worker vĩnh viễn → DepthExceeded giả | Đã chạy | `4b033ef` |
| Low | Lint cho qua file test rỗng; budget thread con; Figma CSS injection + token qua redirect; reviewer máy được tính; zombie ghi qua proxy | Đã chạy / lập luận | `1b53fe3`, `a56ce48`, `976ea27`, `e820f15`, `954a6b1` |

Lỗi vòng 1 tại vòng 2: injection, zombie, budget race, nháp LLM → **đóng**; lint lách test, `sign()` → **chưa đóng hẳn** (đóng ở các commit trên).

## Việc còn mở
- Proxy/handle chỉ chống dùng nhầm, không phải sandbox; cô lập thật cần process/container riêng cho worker.
- Depth guard còn lách được qua `_thread.start_new_thread` (đã ghi docstring).
