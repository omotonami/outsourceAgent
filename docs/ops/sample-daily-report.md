# Mẫu báo cáo ngày (bản `--redact`) — chạy tay end-to-end với `--mock`

Sinh ngày 2026-11-04 trên store tạm (`OA_EVENTSTORE_PATH=/tmp/...`), brief giả có tên client "Acme Secret Bank"
(đã bị lọc khỏi bản dưới). Không ký gate nào (bot không ký) → dự án dừng ở `AWAIT_G1`.

```bash
python -m src.ops.doctor
python -m src.ops.project new --brief brief.yaml --id P-20261104-0900
python -m src.ops.project run --id P-20261104-0900 --mock          # → AWAIT_G1, in "CẦN NGƯỜI LÀM"
python -m src.ops.project huddle --id P-20261104-0900 --phase presales --mock
python -m src.ops.project list
python -m src.ops.prune --dry-run && python -m src.ops.prune
python -m src.ops.report daily --all --redact --out report.md
```

Token/chi phí là số của sender mock (1200 in / 600 out mỗi lần gọi), không phải số thật.

---

## Báo cáo ngày — 2026-11-04

Tạo lúc 2026-11-04 06:44 (UTC+0700) từ event store · **BẢN REDACT** (chỉ mã + trạng thái + số liệu)

| Project | State | Task xong | Gate chờ | Risk H/C mở | Task blocked | Token | USD ước tính |
|---|---|---|---|---|---|---|---|
| P-20261104-0900 | AWAIT_G1 | 0/0 | G1 | 0 | 0 | 10800 | 0.0756 |

### Việc cần người quyết (tất cả project)

- **P-20261104-0900**: Ký G1 (Sales Lead (giá)) qua dashboard gate_ui — người, không phải bot.
- **P-20261104-0900**: 3 câu hỏi Clarify chưa trả lời — hỏi client / PM.
- **P-20261104-0900**: Chưa đặt budget token (config/budgets.yaml per_project_tokens = null) — chủ repo đặt số.

### P-20261104-0900

- State: **AWAIT_G1**
- Gate đã ký: (chưa) · gate đang chờ: G1

#### Task theo trạng thái

(chưa có task_backlog)

#### Risk mở High/Critical

- High: 0 · Critical: 0

#### BLOCKED / escalate

- Task blocked: 0
- Sự kiện BLOCKED (tổng): 0 · ESCALATED: không

#### Token & chi phí ước tính

- Tổng: 10800 token (7200 in / 3600 out) · 6 lần gọi · ~0.0756 USD (đơn giá config/pricing.yaml, chỉ ước tính)
- sonnet: 10800 token · 6 lần
- Theo agent: a1_bu 1800, a2_estimation 1800, a3_proposal 1800, huddle:a1_bu 3600, huddle:a2_estimation 1800
- Budget: chưa đặt (null)

#### Hoạt động 24h qua

- 20 bản ghi · lần cuối 2026-11-04 06:44 (UTC+0700)
- Theo loại: event 6, zone_append 7, zone_write 7
- Theo agent: a1_bu 7, a2_estimation 3, a3_proposal 2, huddle 1, huddle:a1_bu 2, huddle:a2_estimation 1, intake 1, orchestrator 3
- Zone được ghi: business_context, decision_records, estimate, huddle_log, lead_brief, project_status, proposal, risk_register

#### Huddle / Clarify

- decision_records: 1 · câu hỏi Clarify chờ: 3

#### Cần người quyết

- Ký G1 (Sales Lead (giá)) qua dashboard gate_ui — người, không phải bot.
- 3 câu hỏi Clarify chưa trả lời — hỏi client / PM.
- Chưa đặt budget token (config/budgets.yaml per_project_tokens = null) — chủ repo đặt số.
