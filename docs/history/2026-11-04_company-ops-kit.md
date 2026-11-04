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
- **Ký gate cho project thật chưa có đường:** `gate_ui` của dashboard ký board demo DASH trong RAM, không ghi `sign`
  vào event store. Dự án tạo bằng `src.ops.project` sẽ dừng ở `AWAIT_G1`. Test mô phỏng chữ ký người bằng
  `Blackboard.sign(..., via="gate_ui")` trong test. Bot không tự thêm đường ký (anti-job).
- Sau ESCALATED chưa có lệnh gỡ/quay lại — người quyết thiết kế.
- `run` ghi `architecture` + `task_backlog` hộ runner `b4_tech_lead` (runner không tự ghi), đúng danh tính writer
  zone như `src/demo/dry_run.py`.

## Việc còn mở
- Cách người ký gate cho project trong event store (có xác thực người ở server).
- Đặt số `config/budgets.yaml` (per_project_tokens, dispatcher.worker_tokens) sau khi có baseline.
- Scanner bảo mật thật (sast / dependency / secret) — hiện stub.
- Vendor Three.js nếu mạng công ty chặn CDN.
