# Runbook vận hành hằng ngày (Chief / Claude Code ở account công ty)

Đối tượng: chủ repo + Claude Code chạy trong repo này ở account công ty. Skill tổng: `.claude/skills/chief-daily/`.
Mọi lệnh chạy ở gốc repo, venv đã bật. Event store: `config/runtime.yaml → eventstore.path`
(mặc định `data/runtime/events.sqlite3`) hoặc env `OA_EVENTSTORE_PATH` — CLI `src.ops` và dashboard phải trỏ cùng file.

## Lịch một ngày
| Lúc | Việc | Lệnh | Ai |
|---|---|---|---|
| Sáng | Kiểm môi trường | `python -m src.ops.doctor` | Claude chạy, FAIL → báo người |
| Sáng | Báo cáo ngày (nội bộ) | `python -m src.ops.report daily --all --out docs/reports/$(date +%F).md` | Claude chạy, tóm tắt "Việc cần người quyết" |
| Sáng | Ký gate đang chờ | dashboard `gate_ui` (`uvicorn src.server.app:app`) | **Người** |
| Trong ngày | Nhận việc mới | `python -m src.ops.project new --brief data/runtime/briefs/x.yaml` → `run --id P-...` | Claude (brief do người cung cấp) |
| Trong ngày | Huddle khi người yêu cầu | `python -m src.ops.project huddle --id P-... --phase presales\|discovery` | Claude, người quyết kết quả |
| Trong ngày | Tạo bot mới | soạn answers → người chạy `python -m src.factory.new ... --write --create-branch` | Claude soạn, **người** xác nhận |
| Chiều | Chạy tiếp dự án không ở gate | `python -m src.ops.project list` → `run --id P-...` | Claude |
| Chiều | Bản mang ra ngoài (nếu cần) | `python -m src.ops.report daily --all --redact --out docs/reports/$(date +%F)-redact.md` | Claude tạo, người đọc lại rồi mới gửi |
| Tối | Retention (PDPA) | `python -m src.ops.prune` (bản nháp cũ hơn `retention.drafts_days` = 30) | Claude |
| Khi đóng dự án | Xoá hay trả dữ liệu client | `python -m src.ops.prune --closed-project P-...` | **Người quyết**, rồi mới chạy |

`project run` exit code: 0 = dừng bình thường (gate / released), 2 = BLOCKED / ESCALATED (cần người), 1 = lỗi đầu vào.

## Gate
- Gate G1–G4 chỉ người ký qua `gate_ui`. `src/ops` không có lệnh ký và không ghi `gate_signatures`; test
  `tests/contract/test_ops.py::test_no_gate_signing_path_in_ops` giữ điều này.
- **Khoảng trống hiện tại (2026-11-04):** `gate_ui` trên dashboard (`POST /api/gate/...`) ký board demo DASH trong RAM,
  chưa ký vào event store cho project tạo bằng `src.ops.project`. Vì vậy dự án thật sẽ dừng ở `AWAIT_G1` cho tới khi
  chủ repo quyết cách ký gate cho project (vd gate_ui ghi `sign` vào event store, có xác thực người ở server).
  Bot **không** tự thêm đường ký (anti-job của đợt `2026-11-04_company-ops-kit`).

## Xử lý sự cố
| Triệu chứng | Nguyên nhân thường gặp | Làm gì |
|---|---|---|
| `dừng: ESCALATED` | Agent fail schema 2 lần (escalate) hoặc vượt budget | Xem `report daily` → Risk mở / BLOCKED-escalate. Người quyết: sửa brief/spec rồi tạo project mới, hoặc nâng budget (chủ repo). CLI **không** tự gỡ ESCALATED. |
| `dừng: BLOCKED — guard từ chối ...` | Guard Code (vd `discovery_ready`: AC < 95% Given/When/Then; `presales_ready`: assumption chưa vào proposal) | Đọc lý do; người xử lý đầu vào. **Không nới guard** (luật 4). Chạy lại `run`. |
| `BLOCKED — task blocked: [...]` | Dispatcher: task fail QC quá `max_build_loops`, KILL quá ttl, dep blocked | `report daily` (bản nội bộ) có lý do từng task. Tech Lead người quyết. |
| `BLOCKED — lỗi khi chạy: ...API key / Connection` | Thiếu `ANTHROPIC_API_KEY`, proxy/mạng công ty | `python -m src.ops.doctor`; đặt key; kiểm proxy. State không đổi, chạy lại được. |
| Vượt budget | `config/budgets.yaml per_project_tokens` có số và đã chạm | Dự án → ESCALATED. Chủ repo quyết nâng số (sửa config + docs/history). Bot không tự đặt số. |
| Budget null | Chưa đặt số | Không có trần token. `doctor` WARN, report ghi "chưa đặt". Chủ repo đặt sau khi có baseline. |
| SSE / Phòng 3D không LIVE | Dashboard đọc file store khác; proxy đệm SSE | Kiểm `OA_EVENTSTORE_PATH` giống nhau ở 2 terminal; thử `http://127.0.0.1:8000`; trang tự rơi về poll 2s. |
| Phòng 3D trắng | CDN `cdn.jsdelivr.net` (Three.js) bị chặn | Dashboard/`/api/*` vẫn dùng được. Vendor Three.js cần chủ repo duyệt. |
| `project new` lỗi brief | Thiếu `client` / `source` / `received_at` | Hỏi người bổ sung, không tự bịa. |
| Factory `--write` từ chối | Không có TTY (Claude chạy) | Đúng thiết kế: người tự chạy trong terminal của họ. |

## Ranh giới dữ liệu
- Ở lại account công ty: brief (`data/runtime/briefs/`), event store, `eval/runs/` (trace token), `docs/reports/` (bản nội bộ).
- Ra ngoài: chỉ `report daily --redact` (bỏ tên client, brief, nội dung zone sản phẩm, text risk/câu hỏi, đường dẫn file;
  mã project chứa chuỗi brief → bí danh `P-anon-…`). Người đọc lại trước khi gửi.
- Không `git push` lên remote cá nhân (`.claude/settings.json` deny).

## Ghi lịch sử
Mọi thay đổi file trong repo (code, specs, config, docs) → `docs/history/` cùng commit + `history_check` (luật 9).
Chạy dự án / báo cáo / prune không đổi file git → không cần ghi.
