# Huddle (H3 — đề án 0002)

Chế độ khám phá cho PRESALES & DISCOVERY: bắt mơ hồ **trước** gate, không phải ở gate.

## Trigger — Code, không LLM (`src/orchestrator/huddle_trigger.py`)
- `check(board, "PRESALES")`: `business_context.ambiguities` ≥ `huddle.presales_ambiguity_threshold` (config bắt buộc; thiếu → `ConfigError`).
- `check(board, "DISCOVERY")`: `conflicts(board)` khác rỗng — story không có screen (`story_refs` chứa
  `id` / `S<n>` / `i_want`), screen trỏ story không tồn tại, NFR (`nfr` ở requirements/architecture/
  business_context) không được story nào phủ (`nfr_refs` hoặc text NFR có trong story).
- Đã có `decision_record` cùng (phase, trigger) → không trigger lại. Trả `{phase, trigger, question, context}`.

## Chạy (`src/agents/huddle.py`)
`huddle.run(board, phase, question, context, ctx, sender=None, trigger=None)`
- Vai theo `huddle.roles` (config): PRESALES a1_bu ↔ a2_estimation, DISCOVERY b1_ba ↔ b2_ux.
- Mỗi vòng: Proposer → Critic. Critic `converged=true` hoặc hết `max_rounds` (config) → Decider chốt.
- Mỗi lượt nói append `huddle_log` `{round, agent, role, summary}` qua `events.record` (chỉ 2 zone được phép).
- Decider phải ra `decision_record` hợp lệ (`specs/04_schemas/decision_record.json`, `chosen` ∈ `options`
  hoặc null). Fail → retry `decider_retries` (config) lần → vẫn fail → `{"error": "SCHEMA_FAIL", "escalate": True}`.
- Budget riêng `huddle.tokens`; vượt (hoặc budget phase của gateway) → dừng, record `chosen=null`,
  `stopped_reason=budget_exceeded`, `open_questions` = câu hỏi gốc + mọi câu hỏi Critic nêu.
- Record append zone `decision_records` (writer duy nhất `huddle`). Mọi lời gọi model qua `llm_client`,
  tier theo entry `huddle` trong routing.

## Ranh giới
- Huddle chỉ append `huddle_log` / `decision_records`. Zone sản phẩm là `owner_write` → Blackboard chặn
  `append`, và `huddle` không phải writer → chặn `write`. Huddle chỉ **đề xuất**.
- `clarify.pending()` gộp `open_questions` của mọi `decision_record` (API giữ nguyên, bỏ trùng).
- Gate: `gate_view.decision_records(board, "G1"|"G2")` → G1 = presales, G2 = discovery. Dashboard trả trong
  `/api/step` (khi tới gate), `state().gate_decisions`, `GET /api/gate/{gid}/decisions`. Không trả transcript.
- Không đổi guard `gates.py`; huddle không ký gate.

## Config (`config/budgets.yaml` → `huddle`)
`max_rounds`, `tokens` (null = không trần riêng), `decider_retries`, `presales_ambiguity_threshold`, `roles.{presales,discovery}`.
Không có default trong code: thiếu khoá → `ConfigError` (`src/common/config.py`).

Test: `tests/contract/test_huddle.py`.
