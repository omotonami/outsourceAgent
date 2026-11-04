# START HERE — FLOW (outsourceAgent) ở account công ty

## Cài đặt (5 phút) — từ git bundle (cách CHÍNH: có đủ lịch sử, `history_check` chạy được)
```bash
git clone outsourceAgent-company.bundle -b feat/v3-bot-team outsourceAgent && cd outsourceAgent
python3 -m venv .venv && source .venv/bin/activate   # Python ≥ 3.10 (macOS: /opt/homebrew/bin/python3.12)
pip install -r requirements.txt
python -m src.ops.doctor                          # checklist môi trường; FAIL = sửa trước
python -m pytest -q                               # toàn bộ test offline, không cần model
python -m src.tools.checks.history_check --base b7731d3   # luật 9 — cần .git
claude                                            # đăng nhập Claude Code (model chạy qua máy đã đăng nhập); đọc CLAUDE.md + .claude/
```
`outsourceAgent-company.zip` chỉ để đọc nhanh: **không có `.git`** → `history_check` báo lỗi (rc 3).
Folder đã copy/giải nén sẵn không có `.git`: clone lại như trên, hoặc gắn lịch sử tại chỗ:
`git init -b _init && git fetch <đường dẫn>/outsourceAgent-company.bundle 'refs/heads/*:refs/heads/*' &&
git symbolic-ref HEAD refs/heads/feat/v3-bot-team && git reset` (giữ file đang có; `git status` = khác biệt).
Chạy thử không cần model: thêm `--mock` vào `project run` / `project huddle` / `scheduler run`; server `MOCK=1`.

## Mở 1 trang là đủ — Control Room
```bash
uvicorn src.server.app:app                 # terminal 1 → http://localhost:8000/  (Control Room)
python -m src.ops.scheduler run            # terminal 2 — chạy nền MỌI dự án tới gate / ESCALATED / BLOCKED
```
Trang `/`: trái Phòng 3D tất cả dự án (`!` = ESCALATED → bấm mở Sự cố), phải tab **Chat CoS** (hỏi, ＋ Giao việc,
＋ Giao việc phụ) · **Việc** · **Gate** · **Sự cố** · **Token**. Ký gate / gỡ sự cố / tạo dự án: gõ tên + `XAC NHAN`.
Chi tiết: `docs/control-room.md`. Dashboard cũ: `/legacy`. Dùng lệnh dưới đây khi không mở trang.

## 4 việc hằng ngày
| Việc | Nói với Claude / skill | Lệnh |
|---|---|---|
| 1. Nhận việc → chạy dự án | `flow-new-project` | `python -m src.ops.project new --brief b.yaml` → `run --id P-...` |
| 2. Báo cáo tiến độ | `flow-daily-report` | `python -m src.ops.report daily --all [--redact]` |
| 3. Theo dõi + ra lệnh | `flow-room` | `uvicorn src.server.app:app` → http://localhost:8000/ (Control Room) |
| 4. Tạo bot mới | `flow-new-bot` | người chạy `python -m src.factory.new --spec x.yaml --write --create-branch` |
Cả ngày: skill `chief-daily` · lịch + sự cố: `docs/ops/runbook-daily.md`.
Khác: `project list` · `project status --id` · `project huddle --id --phase presales|discovery` (gọi tay)
· `project task add --id --role --title [--kind side]` (việc phụ) · `scheduler run [--once] [--mock]` · `scheduler status`
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
Chỉ NGƯỜI ký/trả lại (CLAUDE.md luật 10) — 2 đường, cùng lõi `src/ops/gate.py`, ghi vào event store:
1. Control Room: `http://localhost:8000/?tab=gate&project=P-...` tab **Gate** → gõ tên + `XAC NHAN`.
2. Terminal của người: `python -m src.ops.gate sign --id P-... --gate G1` (trả lại: `reject ... --reason "..."`).
Ký xong → scheduler tự chạy tiếp (không có scheduler: `python -m src.ops.project run --id P-...`). Claude không chạy lệnh/API ký (settings deny).
ESCALATED: cũng chỉ NGƯỜI gỡ — `python -m src.ops.escalation resolve --id P-... --action retry|backtrack:<PHASE>|abandon`
(TTY, gõ tên + `XAC NHAN`) hoặc Control Room tab **Sự cố** (nút Retry / Quay về phase / Huỷ); xem `docs/ops/runbook-daily.md`.

## Ranh giới dữ liệu
- Dữ liệu công ty (brief, spec, code, tên client, event store, báo cáo nội bộ) **ở lại account/máy công ty**.
- Ra ngoài chỉ bản `python -m src.ops.report daily --redact` (mã project + trạng thái + số liệu), đọc lại trước khi gửi.
- Brief để ở `data/runtime/briefs/` (gitignore). Báo cáo ở `docs/reports/` (gitignore). Không push lên remote cá nhân.

## Việc người phải tự làm khi cài
1. Máy đã đăng nhập Claude Code (lệnh `claude`). 2. Chủ repo đặt số `config/budgets.yaml` (đang null = không trần).
3. Dán `docs/ops/chief-prompt.md` vào Project "Chief" trên claude.ai công ty. 4. Người ký gate biết 2 đường ở trên.
