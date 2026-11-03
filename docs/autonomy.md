# Autonomy Ladder (H4 — đề án 0002) — chỉ L0–L2

**L3 Auto-gate (gate tự ký, kể cả G3): BẢO LƯU** theo quyết định chủ repo. Không có code path nào ký gate;
`HARD_MAX = 2` trong `src/orchestrator/autonomy.py` — config `max_level: L3` hay state sửa tay vẫn bị khoá ở L2.

| Mức | Nghĩa | Trigger được chạy | Người duyệt |
|---|---|---|---|
| L0 Manual | Người kích hoạt từng lần (mặc định agent mới / chưa có dữ liệu) | `manual` | mọi output |
| L1 Skill | Chạy với skill đã promote, khi người kích hoạt | `manual` | mọi output |
| L2 Routine | Tự chạy theo trigger | `manual`, `routine`, `schedule`, `event` | ở gate như cũ |

Mọi số ở `config/autonomy.yaml` (ngưỡng, số mức hạ, trigger, cặp ghim — `e1_pm` ghim L0).

## Luồng (`Autonomy`, theo cặp `(agent_id, task_type)`)
- State: `eval/runs/autonomy_state.json` (inject `state_path` cho test). Không có cặp = L0.
- `record_review(agent, task_type, gate, reviewer, edited=False)` — ghi `eval/runs/autonomy_reviews.jsonl`
  khi người duyệt ở gate. Chỉ ghi log, **không ký gate**.
- `gate_feedback.record_edit(..., task_type=...)` ghi `task_type` → gate edit chỉ hạ đúng cặp
  `(agent, task_type)`; thiếu `task_type` → hạ mọi task_type của agent (thận trọng, như cũ).
- `evaluate(board, eval_results)` đọc reviews + `gate_feedback.jsonl` + `revision_log` (kind=edit) +
  regression (`eval/runs/eval_regression.jsonl` hoặc tham số):
  - **Nâng = đề xuất**: đủ `promote.consecutive_approvals.L<n>` lần duyệt liên tiếp không sửa kể từ lần
    đổi mức gần nhất → `pending`. Chưa có hiệu lực.
  - **Hạ = tự động, ngay**: revision / gate edit / regression → hạ `demote.on_*` mức, huỷ pending,
    ghi `risk_register` (phase `autonomy`). Mỗi bằng chứng chỉ tính một lần (mốc `since`).
- `promote(agent, task_type, approved_by=<người>)` — chỉ khi có pending; từ chối approver rỗng, không phải
  người (danh sách chung `src/governance/confirm.py`: agent/writer/hệ thống/`gate_ui`/`auto*` + `reject_approvers`)
  hoặc chính agent; từ chối đích > L2. `reject(...)` bác đề xuất.
- `report_incident(agent, task_type, reason, board)` → về `demote.on_incident` (L0).
- `can_run(agent, task_type, trigger)` → `{allowed, level, review, reason}`.

## Tích hợp
- Dispatcher: `Dispatcher(..., trigger="manual", autonomy=None)`. Trước mỗi spawn hỏi
  `can_run(task.agent or "c1_dev", task.type or "build_task", trigger)`; bị chặn → task giữ `ready`,
  sự kiện `BLOCKED` `reason="autonomy"` (1 lần/run), `summary.autonomy_denied`. Mặc định `manual` = hành vi cũ.
- Gate UI (`src/server/app.py`): `POST /api/gate/approve` (không `edited`) → sau khi NGƯỜI ký qua `gate_ui`,
  gọi `record_review(agent, gate_task_types[gate], gate, reviewer, edited=False)` cho mỗi agent có output
  trong phase của gate. Chỉ ghi log, không ký thay. `edited=true` → không ghi (sửa đi qua `gate_feedback`).
  Ghi review lỗi (`OSError`/`ValueError`/`ConfigError`) → chữ ký vẫn hiệu lực, request trả bình thường,
  ghi `risk_register` (writer `gate_ui`, phase `autonomy`) để người xử lý; lỗi khác vẫn nổi lên.
- Huddle: `huddle_trigger.check(board, phase, autonomy=ax, trigger="routine")` → agent `huddle`,
  task_type = phase. Không truyền `autonomy` = hành vi cũ.

Không đổi `gates.py`, guard, specs. Test: `tests/contract/test_autonomy.py`.
