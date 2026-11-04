# 2026-11-04 — Bộ vận hành hằng ngày cho account công ty (company ops kit)

- Nhánh: `feat/v3-bot-team` · từ `64f7259` (411 test) → commit của đợt (prefix `feat(ops)`), **432 test**,
  `agent_lint --all` 14/14, `history_check --base b7731d3` PASS.
- Giao việc: Chief of Staff của chủ repo (Daipc) giao bot "Gói" — đóng gói repo thành bộ vận hành hằng ngày cho
  Claude Code chạy ở account CÔNG TY (TechTus).

## Quyết định / ranh giới do chủ repo đặt (theo đề bài giao việc, 2026-11-04)
| Mục | Nội dung |
|---|---|
| 4 việc hằng ngày | (1) nhận việc → chạy dự án · (2) báo cáo tiến độ hằng ngày · (3) mở phòng 3D · (4) tạo bot mới bằng Factory |
| Ranh giới dữ liệu | Dữ liệu công ty ở lại account công ty; chỉ bản tóm tắt đã lọc (không tên client, không code, không spec chi tiết) mới ra ngoài |
| Huddle | Gọi tay (`project huddle`), không nối vào state machine |
| Anti-job | Không nới gate/guard, không thêm đường ký gate, không L3, không tự đặt budget/rate thật, không push/PR/remote, không tự điền danh tính người |

## Thay đổi
| Mục | Đã đổi gì | Vì sao | File chính | Test |
|---|---|---|---|---|
| CLI dự án | `python -m src.ops.project new\|run\|huddle\|list\|status`. `new`: validate lead_brief (YAML / MD front matter) → ghi qua Blackboard (writer `intake`) + `project_status` INTAKE. `run`: `Blackboard.resume` từ event store, state = `project_status.state`, chạy agent của phase (idempotent theo zone), BUILD qua Dispatcher, DỪNG ở AWAIT_Gx / ESCALATED / BLOCKED và in "CẦN NGƯỜI LÀM". Sổ budget nạp lại token đã dùng từ event store. Scanner bảo mật còn stub → ghi risk High "CHƯA quét thật" (không giả vờ đã quét). `--mock`: sender mock (`src/ops/mock.py`) qua gateway thật | Chief chạy dự án qua nhiều ngày / nhiều process | `src/ops/project.py`, `src/ops/pipeline.py`, `src/ops/common.py`, `src/ops/mock.py` | 13 |
| Không có đường ký gate | `src/ops` không gọi `sign`, không ghi `gate_signatures`; CLI không có lệnh ký; test quét mã nguồn | Luật 10, anti-job | `tests/contract/test_ops.py` | 2 |
| `Blackboard.resume` | Dựng lại từ log rồi bật ghi xuyên vào cùng store (lượt ghi mới vẫn kiểm quyền + schema) | `run` tiếp ở process mới | `src/blackboard/store.py` | (trong test_ops) |
| Báo cáo ngày | `python -m src.ops.report daily [--id\|--all] [--out] [--redact] [--now]`: Markdown deterministic — state, task theo trạng thái, gate, risk High/Critical mở, BLOCKED/escalate, token + chi phí ước tính (`config/pricing.yaml`), hoạt động 24h, huddle/clarify, việc cần người quyết. `--redact`: chỉ mã + trạng thái + số liệu; lớp chặn cuối thay mọi chuỗi lead_brief bằng `[ẩn]`, mã project chứa chuỗi brief → `P-anon-<sha8>` | Báo cáo nội bộ + bản mang ra ngoài theo ranh giới dữ liệu | `src/ops/report.py`, `docs/ops/sample-daily-report.md` | 3 |
| Retention | `python -m src.ops.prune [--days N] [--dry-run]`: xoá **bản nháp** (zone_write đã có bản mới hơn) cũ hơn `retention.drafts_days`; bản mới nhất giữ lại để `run` vẫn chạy tiếp. `--closed-project P-...`: xoá toàn bộ project đã RELEASED/ESCALATED (người quyết, `delete_or_return`); đang chạy → từ chối. Sau xoá: WAL checkpoint + VACUUM. EventStore thêm `prune_superseded()`, `vacuum()` | PDPA, chạy hằng ngày; xoá theo dòng thô sẽ làm mất lead_brief của dự án dài ngày | `src/ops/prune.py`, `src/blackboard/eventstore.py` | 2 |
| Doctor | `python -m src.ops.doctor`: Python ≥3.10, gói, `ANTHROPIC_API_KEY` có/không (không in giá trị), budgets null → WARN, pricing có số, event store ghi được, `.claude/settings.json`, ghi chú CDN Three.js + gate | Kiểm môi trường đầu ngày | `src/ops/doctor.py` | 2 |
| Claude Code | `.claude/skills/` (chief-daily, flow-new-project, flow-daily-report, flow-room, flow-new-bot) — nêu chỗ DỪNG hỏi người (gate, Factory TTY, ESCALATED/BLOCKED, dữ liệu ra ngoài). `.claude/settings.json`: allow lệnh `src.ops`/pytest/lint/history_check/uvicorn; ask prune `--closed-project`, `src.factory.new`, git commit, sửa `specs/` `config/` `CLAUDE.md`; deny `git push`, `rm -rf`, `gh pr`/`gh repo`, `git remote add`, đọc `.env` | Chief ở account cty dùng ngay | `.claude/` | — |
| Tài liệu | `START_HERE.md` (≤60 dòng), `docs/ops/runbook-daily.md`, `docs/ops/chief-prompt.md`; CLAUDE.md thêm mục "Vận hành hằng ngày (Chief)" (10 luật giữ nguyên); `config/budgets.yaml` thêm comment hướng dẫn (số giữ null); `.gitignore` thêm `docs/reports/` | | | — |

Số test sau đợt: **432** (411 + 21 `tests/contract/test_ops.py`).

## Vòng 2 — phản hồi từ bot Chief ở account công ty (2026-11-04)
Phản hồi (qua Chief of Staff): bản zip (git archive) không có `.git` → `history_check` không chạy; HANDOFF còn đường dẫn cũ.

| Mục | Đã đổi gì | File chính | Test |
|---|---|---|---|
| Cài từ bundle | START_HERE: cách chính = `git clone outsourceAgent-company.bundle -b feat/v3-bot-team`; zip ghi rõ không có `.git`; cách gắn lịch sử cho folder đã copy (`git init -b _init` + fetch bundle + `symbolic-ref` + `reset`, đã thử: `git status` sạch) | `START_HERE.md` | — |
| history_check không phải git repo | rc 3 + hướng dẫn clone từ bundle thay vì lỗi git khó hiểu | `src/tools/checks/history_check.py`, `tests/unit/test_history_check.py` | 2 |
| HANDOFF | Bỏ đường dẫn cũ `~/Downloads/outsourcing-agents-v2` / tên zip cũ, trỏ START_HERE + bundle | `docs/HANDOFF.md` | — |

Số test sau vòng 2: **434**.

## Phát hiện cần chủ repo quyết
- ~~**Ký gate cho project thật chưa có đường**~~ → đã làm ở Vòng 3 (chủ repo duyệt 2026-11-04 07:4x).
- Sau ESCALATED chưa có lệnh gỡ/quay lại — người quyết thiết kế.
- `run` ghi `architecture` + `task_backlog` hộ runner `b4_tech_lead` (runner không tự ghi), đúng danh tính writer
  zone như `src/demo/dry_run.py`.

## Vòng 3 — người ký gate cho dự án thật (bot "Cổng", 2026-11-04)
Quyết định của chủ repo (2026-11-04 07:4x, qua Chief of Staff): duyệt 2 đường cho NGƯỜI ký/trả lại G1–G4 của dự án
trong event store — (1) dashboard gate_ui theo dự án, (2) lệnh terminal tương tác do người tự gõ. Máy/Claude không ký.
Anti-job giữ: không nới `gates.py`/guard, không đường ký cho máy, không auto-ký, không L3, không nối Huddle tự động,
không push/PR/remote, không tự điền danh tính người.

| Mục | Đã đổi gì | Vì sao | File chính | Test |
|---|---|---|---|---|
| Lõi gate | `src/ops/gate.py`: `view()` (gate đang chờ + dữ liệu trình gate + `packet_hash`), `sign()` / `reject()`: dựng board từ event store, kiểm đúng `AWAIT_Gx` + chưa ký, người ký qua `confirm.require_human` (máy/agent/auto-*/gate_ui → từ chối), kênh ∈ {dashboard, terminal}, `expected_hash` lệch → từ chối. Ký: thử guard của transition "như thể đã ký" (không đổi `gates.py`) → G4 còn finding High/Critical thì không ký; `Blackboard.sign(gate, by=<người>, via="gate_ui")` → event store; trace_log DECISION; duyệt không sửa → `autonomy.record_review` (lỗi log → risk_register), có sửa → `gate_feedback.record_edit`. Trả lại: `Orchestrator.backtrack` + `project_status` + `gate_feedback.record_edit` (có `task_type`) | Một lõi cho cả 2 đường | `src/ops/gate.py`, `src/orchestrator/gate_view.py` (`packet`, `GATE_AGENTS`, `BACKTRACK`, `room_gate`) | 47 (`tests/contract/test_ops_gate.py`) |
| Chạy lại sau trả lại | `project run` đọc DECISION reject (`rerun=[phase]`) → chạy lại agent của phase đó dù zone đã có, xong ghi `rerun_done` | Không thì `run` quay lại gate ngay với output cũ | `src/ops/pipeline.py` | (trong test_ops_gate) |
| Dashboard | `GET /api/projects/{id}/gate`; `POST /api/projects/{id}/gate/{gate}/{approve\|reject}` (body: `signer`, `typed_confirm == "XAC NHAN"`, `packet_hash`, `reason`, `backtrack_to`, `edited`, `note`; thiếu XAC NHAN / tên máy → 400, sai gate / hash / guard → 409). Tab "Gate dự án" trong `index.html` (`/?project=P-...`). Endpoint ghi DUY NHẤT của server; test eventstore cập nhật để chỉ cho đúng route này | Đường 1 | `src/server/app.py`, `src/server/static/index.html`, `tests/contract/test_eventstore.py` | |
| Phòng 3D | Chế độ project hiện gate đang chờ (thanh vàng + agent `waiting_gate`) từ `project_status` cùng store | | `src/orchestrator/room_stream.py`, `room.html` | |
| Terminal | `python -m src.ops.gate sign\|reject --id --gate [--reason --backtrack-to --edited --note --db]`: bắt `stdin.isatty()`; in tóm tắt + hash; người gõ tên rồi `XAC NHAN`; không nhận tên qua tham số; không TTY / tên máy / gõ sai / EOF / hash đổi giữa chừng → rc 2, không ghi gì. Đã thử với TTY thật (`script`) | Đường 2 | `src/ops/gate.py` | |
| Chặn Claude | `.claude/settings.json` deny: `python[3] -m src.ops.gate`, `.venv/bin/python -m ...`, `python src/ops/gate.py`, `python -c *src.ops*gate*`, `script`/`expect`/`unbuffer` (giả TTY), curl/wget/http `*api/projects/*/gate*`, WebFetch localhost/127.0.0.1. Skills (flow-new-project, flow-room, chief-daily, flow-daily-report) ghi: tới gate DỪNG, đưa người link/lệnh, không chạy. Test `test_no_gate_signing_path_in_ops` đổi: `.sign(` chỉ được có trong `src/ops/gate.py` | Luật 10 | `.claude/`, `tests/contract/test_ops.py` | |
| Tài liệu | CLAUDE.md (mục Vận hành, 10 luật giữ nguyên), START_HERE, runbook mục Gate + sự cố, chief-prompt, doctor/report/status/`run` in đúng 2 đường | | | |

Số test sau vòng 3: **481** (434 + 47). `agent_lint --all` 14/14.

Giới hạn đã biết (vòng 3):
- Chặn Claude là rào cấu hình (settings deny theo mẫu lệnh) + TTY + XAC NHAN, không phải xác thực mật mã: server chạy
  localhost không đăng nhập; tên người là tên người gõ (không kiểm danh tính thật).
- Agent chạy lại sau "trả lại" chưa nhận lý do trả lại làm input (schema agent không có trường này). G3 trả lại về BUILD:
  Dispatcher không đặt lại task đã `await_merge` → chạy lại BUILD hầu như không đổi gì.

## Việc còn mở
- Đăng nhập / xác thực người ở dashboard nếu mở ra ngoài localhost; đưa lý do trả lại vào input agent; G3 trả lại → đặt lại task.
- Đặt số `config/budgets.yaml` (per_project_tokens, dispatcher.worker_tokens) sau khi có baseline.
- Scanner bảo mật thật (sast / dependency / secret) — hiện stub.
- Vendor Three.js nếu mạng công ty chặn CDN.
