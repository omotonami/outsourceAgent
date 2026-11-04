# Runbook vận hành hằng ngày (Chief / Claude Code ở account công ty)

Đối tượng: chủ repo + Claude Code chạy trong repo này ở account công ty. Skill tổng: `.claude/skills/chief-daily/`.
Mọi lệnh chạy ở gốc repo, venv đã bật. Event store: `config/runtime.yaml → eventstore.path`
(mặc định `data/runtime/events.sqlite3`) hoặc env `OA_EVENTSTORE_PATH` — CLI `src.ops` và dashboard phải trỏ cùng file.

## Lịch một ngày
| Lúc | Việc | Lệnh | Ai |
|---|---|---|---|
| Sáng | Kiểm môi trường | `python -m src.ops.doctor` | Claude chạy, FAIL → báo người |
| Sáng | Báo cáo ngày (nội bộ) | `python -m src.ops.report daily --all --out docs/reports/$(date +%F).md` | Claude chạy, tóm tắt "Việc cần người quyết" |
| Sáng | Ký / trả lại gate đang chờ | dashboard tab "Gate dự án" hoặc `python -m src.ops.gate sign\|reject --id P-... --gate Gx` (terminal của người) | **Người** — Claude không chạy |
| Trong ngày | Nhận việc mới | `python -m src.ops.project new --brief data/runtime/briefs/x.yaml` → `run --id P-...` | Claude (brief do người cung cấp) |
| Trong ngày | Huddle khi người yêu cầu | `python -m src.ops.project huddle --id P-... --phase presales\|discovery` | Claude, người quyết kết quả |
| Trong ngày | Tạo bot mới | soạn answers → người chạy `python -m src.factory.new ... --write --create-branch` | Claude soạn, **người** xác nhận |
| Chiều | Chạy tiếp dự án không ở gate | `python -m src.ops.project list` → `run --id P-...` | Claude |
| Chiều | Bản mang ra ngoài (nếu cần) | `python -m src.ops.report daily --all --redact --out docs/reports/$(date +%F)-redact.md` | Claude tạo, người đọc lại rồi mới gửi |
| Tối | Retention (PDPA) | `python -m src.ops.prune` (bản nháp cũ hơn `retention.drafts_days` = 30) | Claude |
| Khi đóng dự án | Xoá hay trả dữ liệu client | `python -m src.ops.prune --closed-project P-...` | **Người quyết**, rồi mới chạy |

`project run` exit code: 0 = dừng bình thường (gate / released / abandoned), 2 = BLOCKED / ESCALATED (cần người), 1 = lỗi đầu vào.

## Gate (chủ repo duyệt 2026-11-04 07:4x: dashboard theo dự án + terminal tương tác do người gõ)
Chỉ NGƯỜI ký/trả lại. Cả hai đường đi qua lõi `src/ops/gate.py` → `Blackboard.sign(gate, by=<tên người>, via="gate_ui")`
→ event store; `project run` (process nào cũng được) đọc chữ ký và đi tiếp.

| Đường | Người làm | Kiểm |
|---|---|---|
| Dashboard | `uvicorn src.server.app:app` → `http://localhost:8000/?project=P-...` → tab **Gate dự án**: xem highlights (ASSUMPTION, exclusions, open_questions, finding High...), zone output, decision_records (không transcript) → gõ tên + `XAC NHAN` → Duyệt / Trả lại (lý do + phase quay về) | `POST /api/projects/{id}/gate/{gate}/{approve\|reject}`: thiếu `XAC NHAN` / tên máy → 400; sai gate / dữ liệu đổi (packet_hash) / guard → 409 |
| Terminal | `python -m src.ops.gate sign --id P-... --gate G1 [--edited --note ...]` · `python -m src.ops.gate reject --id P-... --gate G1 --reason "..." [--backtrack-to PRESALES]` → xem tóm tắt + hash → gõ tên → gõ `XAC NHAN` | Bắt TTY; không nhận tên qua tham số; tên máy / gõ sai / EOF / hash đổi giữa chừng → rc 2, không ghi gì |

- **Duyệt không sửa** → `autonomy.record_review` cho agent của gate (lỗi ghi log → risk_register, chữ ký vẫn giữ).
  **Duyệt có sửa** (`--edited` / ô tick) → `gate_feedback.record_edit`.
- **Trả lại** → `Orchestrator.backtrack` (huỷ chữ ký phía sau) + `gate_feedback.record_edit` (có `task_type`); `run` chạy
  LẠI agent của các phase từ chỗ quay về (dù zone đã có) rồi dừng lại ở gate. Agent chưa nhận lý do trả lại làm input.
- **G4** còn finding High/Critical chưa remediation → guard có sẵn từ chối, không ký được (409 / rc 2).
- Claude: tới gate thì DỪNG, đưa người link/lệnh. `.claude/settings.json` deny `python -m src.ops.gate` (mọi biến thể đã
  biết), `script`/`expect`/`unbuffer` (giả TTY), curl/wget/http tới `/api/projects/*/gate`, WebFetch localhost. Đây là
  rào cấu hình + TTY, không phải xác thực mật mã: server chạy localhost, không đăng nhập — ai mở được dashboard là ký được.

## ESCALATED — người gỡ (đợt field-fixes 2026-11-04; cùng chuẩn ký gate, luật 10)
Dự án ESCALATED không tự chạy tiếp. NGƯỜI chọn 1 trong 3 hành động; cả hai đường đi qua lõi `src/ops/escalation.py`
(`resolve`), ghi DECISION vào trace_log + APPEND bản ghi resolution vào risk_register (risk escalate coi như đã đóng).

| Hành động | Khi nào | Kết quả |
|---|---|---|
| `retry` | Lỗi tạm thời / đã sửa nguyên nhân (vd bản sửa parse JSON, đã nâng budget) | State về phase lỗi; `project run` gọi lại agent lỗi với tier cao hơn nếu routing cho phép (`specs/03_routing.yaml escalate_to`, vd a1_bu → opus) |
| `backtrack:<PRESALES\|DISCOVERY\|BUILD\|SECURITY>` | Đầu vào phase trước sai (vd brief/proposal), cần làm lại từ đó | Huỷ chữ ký gate phía sau phase đó; `run` chạy lại agent các phase từ đó tới phase lỗi (dù zone đã có) |
| `abandon` | Dừng hẳn dự án | State `ABANDONED` (đóng, không chạy nữa; dữ liệu giữ — xoá/trả theo `prune --closed-project`, người quyết) |

| Đường | Người làm |
|---|---|
| Terminal của NGƯỜI | `python -m src.ops.escalation resolve --id P-... --action retry` (hoặc `--action backtrack:DISCOVERY`, `--action abandon`, thêm `--note "..."`) → xem tóm tắt + hash → gõ tên → gõ `XAC NHAN`. Bắt TTY; không nhận tên qua tham số; tên máy / gõ sai / EOF / tình trạng đổi giữa chừng → rc 2, không ghi gì |
| Dashboard (API) | `GET /api/projects/{id}/escalation` (lý do, phase lỗi, agent, `options`, `packet_hash`) · `POST /api/projects/{id}/escalation/{retry\|backtrack:PHASE\|abandon}` body `{"signer": "<tên người>", "typed_confirm": "XAC NHAN", "note": "...", "packet_hash": "..."}` → 400 (tên máy / thiếu XAC NHAN / action lạ), 404 (không có dự án), 409 (không ESCALATED / hash đổi). **Chưa có nút UI** — đợt UI bên công ty thêm nút gọi API này |

Sau `retry` / `backtrack`: chạy `python -m src.ops.project run --id P-...`. Claude chỉ đưa lệnh cho người: `.claude/settings.json`
deny `python -m src.ops.escalation` (mọi biến thể đã biết) và curl/wget/http tới `/api/projects/*/escalation*`.

## Xử lý sự cố
| Triệu chứng | Nguyên nhân thường gặp | Làm gì |
|---|---|---|
| `dừng: ESCALATED` | Agent fail schema hết số lượt retry (`config/runtime.yaml → agent_runtime.schema_retries`) hoặc vượt budget | Xem `report daily` / `GET /api/projects/P-.../escalation` (lý do, phase lỗi, agent). **Người** gỡ theo mục "ESCALATED — người gỡ" bên dưới. Máy/Claude không tự gỡ. |
| `dừng: BLOCKED — guard từ chối ...` | Guard Code (vd `discovery_ready`: AC < 95% Given/When/Then; `presales_ready`: assumption chưa vào proposal) | Đọc lý do; người xử lý đầu vào. **Không nới guard** (luật 4). Chạy lại `run`. |
| `BLOCKED — task blocked: [...]` | Dispatcher: task fail QC quá `max_build_loops`, KILL quá ttl, dep blocked | `report daily` (bản nội bộ) có lý do từng task. Tech Lead người quyết. |
| `BLOCKED — lỗi khi chạy: ...API key / Connection` | Thiếu `ANTHROPIC_API_KEY`, proxy/mạng công ty | `python -m src.ops.doctor`; đặt key; kiểm proxy. State không đổi, chạy lại được. |
| Vượt budget | `config/budgets.yaml per_project_tokens` có số và đã chạm | Dự án → ESCALATED. Chủ repo quyết nâng số (sửa config + docs/history). Bot không tự đặt số. |
| Budget null | Chưa đặt số | Không có trần token. `doctor` WARN, report ghi "chưa đặt". Chủ repo đặt sau khi có baseline. |
| Phòng 3D "0 đang làm" ở presales/discovery | Bản trước đợt field-fixes không ghi sự kiện theo agent | Từ đợt này pipeline ghi ASSIGN/RESULT/BLOCKED mỗi lần gọi agent; agent chỉ "đang làm" trong lúc `run` gọi nó (dừng ở gate → waiting_gate) |
| SSE / Phòng 3D không LIVE | Dashboard đọc file store khác; proxy đệm SSE | Kiểm `OA_EVENTSTORE_PATH` giống nhau ở 2 terminal; thử `http://127.0.0.1:8000`; trang tự rơi về poll 2s. |
| Phòng 3D trắng | CDN `cdn.jsdelivr.net` (Three.js) bị chặn | Dashboard/`/api/*` vẫn dùng được. Vendor Three.js cần chủ repo duyệt. |
| `project new` lỗi brief | Thiếu `client` / `source` / `received_at` | Hỏi người bổ sung, không tự bịa. |
| Factory `--write` từ chối | Không có TTY (Claude chạy) | Đúng thiết kế: người tự chạy trong terminal của họ. |
| `src.ops.gate` / dashboard gate từ chối | Không TTY, tên máy, gõ sai `XAC NHAN`, sai gate (đã ký/đã đi tiếp), dữ liệu đổi | Đọc thông báo; tải lại dashboard / chạy lại lệnh. "đã được ký" → chạy `project run`. |

## Quyền Claude (`.claude/settings.json`) — chỉ là lớp PHỤ (review PR #2, 2026-11-04)
- pytest: chỉ allow đúng `pytest -q tests/` và `python -m pytest -q tests/`; mọi dạng khác (thêm `-p`, `--rootdir`,
  `-c`, file ngoài `tests/`…) không có trong allow → Claude Code hỏi người (ask mặc định). Không đặt `pytest:*` vào
  `ask` vì ask được ưu tiên hơn allow (sẽ hỏi cả lệnh chuẩn).
- Sửa/viết `tests/**` → ask (test là bằng chứng; bot không lặng lẽ sửa test cho xanh — luật 4/8).
- Deny `git diff --no-index*` (đọc file bất kỳ ngoài repo, vd `.env`, qua `git diff`).
- **Deny/ask chỉ là lớp phụ**: so khớp chuỗi lệnh, lách được bằng biến thể (khoảng trắng, alias, script trung gian).
  Lớp chính: TTY do người gõ + kiểm danh tính người (`src/governance/confirm.py` — chống dùng nhầm, không phải xác
  thực) + server chỉ nhận ghi JSON từ Host/Origin cục bộ (`dashboard.allowed_hosts`) + review người trước merge.

## Ranh giới dữ liệu
- Ở lại account công ty: brief (`data/runtime/briefs/`), event store, `eval/runs/` (trace token), `docs/reports/` (bản nội bộ).
- Ra ngoài: chỉ `report daily --redact` (bỏ tên client, brief, nội dung zone sản phẩm, text risk/câu hỏi, đường dẫn file;
  mã project chứa chuỗi brief → bí danh `P-anon-…`). Người đọc lại trước khi gửi.
- Không `git push` lên remote cá nhân (`.claude/settings.json` deny).

## Ghi lịch sử
Mọi thay đổi file trong repo (code, specs, config, docs) → `docs/history/` cùng commit + `history_check` (luật 9).
Chạy dự án / báo cáo / prune không đổi file git → không cần ghi.
