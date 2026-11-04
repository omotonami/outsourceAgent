# START HERE — FLOW (outsourceAgent) ở account công ty

## Cài đặt (5 phút) — từ git bundle (cách CHÍNH: có đủ lịch sử, `history_check` chạy được)
```bash
git clone outsourceAgent-company.bundle -b feat/v3-bot-team outsourceAgent && cd outsourceAgent
python3 -m venv .venv && source .venv/bin/activate   # Python ≥ 3.10 (macOS: /opt/homebrew/bin/python3.12)
pip install -r requirements.txt
python -m src.ops.doctor                          # checklist môi trường; FAIL = sửa trước
python -m pytest -q                               # toàn bộ test offline, không cần key
python -m src.tools.checks.history_check --base b7731d3   # luật 9 — cần .git
export ANTHROPIC_API_KEY=...                      # key CÔNG TY, đặt trong shell/.env — không commit
claude                                            # Claude Code đọc CLAUDE.md + .claude/ (skills, permissions)
```
`outsourceAgent-company.zip` chỉ để đọc nhanh: **không có `.git`** → `history_check` báo lỗi (rc 3).
Folder đã copy/giải nén sẵn không có `.git`: clone lại như trên, hoặc gắn lịch sử tại chỗ:
`git init -b _init && git fetch <đường dẫn>/outsourceAgent-company.bundle 'refs/heads/*:refs/heads/*' &&
git symbolic-ref HEAD refs/heads/feat/v3-bot-team && git reset` (giữ file đang có; `git status` = khác biệt).
Chạy thử không cần key: thêm `--mock` vào `project run` / `project huddle`.

## 4 việc hằng ngày
| Việc | Nói với Claude / skill | Lệnh |
|---|---|---|
| 1. Nhận việc → chạy dự án | `flow-new-project` | `python -m src.ops.project new --brief b.yaml` → `run --id P-...` |
| 2. Báo cáo tiến độ | `flow-daily-report` | `python -m src.ops.report daily --all [--redact]` |
| 3. Mở phòng 3D | `flow-room` | `uvicorn src.server.app:app` → http://localhost:8000/room |
| 4. Tạo bot mới | `flow-new-bot` | người chạy `python -m src.factory.new --spec x.yaml --write --create-branch` |
Cả ngày: skill `chief-daily` · lịch + sự cố: `docs/ops/runbook-daily.md`.
Khác: `project list` · `project status --id` · `project huddle --id --phase presales|discovery` (gọi tay)
· `python -m src.ops.prune` (tối, retention).

`project run` chạy tới khi gặp gate (AWAIT_Gx), ESCALATED hoặc BLOCKED thì DỪNG và in "CẦN NGƯỜI LÀM".
Chạy lại được hôm sau, ở process khác: state dựng lại từ event store (`data/runtime/`, gitignore).

## Ai ký gate
| Gate | Người ký | Sau gate |
|---|---|---|
| G1 | Sales Lead (giá) | Discovery |
| G2 | Client + PM (scope) | Build |
| G3 | Tech Lead (merge) | Security |
| G4 | Tech Lead + bảo mật (release) | Released |
Chỉ người ký qua dashboard `gate_ui`. CLI không có lệnh ký; Claude không ký thay (CLAUDE.md luật 10).
**Lưu ý:** hiện gate_ui chỉ ký board demo; ký gate cho project trong event store CHƯA có — xem runbook mục Gate.

## Ranh giới dữ liệu
- Dữ liệu công ty (brief, spec, code, tên client, event store, báo cáo nội bộ) **ở lại account/máy công ty**.
- Ra ngoài chỉ bản `python -m src.ops.report daily --redact` (mã project + trạng thái + số liệu), đọc lại trước khi gửi.
- Brief để ở `data/runtime/briefs/` (gitignore). Báo cáo ở `docs/reports/` (gitignore). Không push lên remote cá nhân.

## Việc người phải tự làm khi cài
1. Đặt `ANTHROPIC_API_KEY` công ty. 2. Chủ repo đặt số `config/budgets.yaml` (đang null = không trần).
3. Dán `docs/ops/chief-prompt.md` vào Project "Chief" trên claude.ai công ty. 4. Quyết cách ký gate cho project thật.
