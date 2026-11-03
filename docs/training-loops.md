# Hai vòng training (học có kiểm soát)

Hệ không tự sửa prompt hay ghi đè `_core` từ hội thoại. Mọi "học" đi qua đúng hai cửa,
đều có người hoặc dữ liệu thật xác nhận.

## Vòng 1 — Feedback ở gate  (`src/feedback/gate_feedback.py`)
1. Người duyệt sửa/bác một mục ở G1..G4.
2. `record_edit(...)` ghi 1 mẫu vào `eval/runs/gate_feedback.jsonl` và append 1 note vào
   Blackboard `risk_register` (source_agent = gate_ui).
3. Hàm trả về một *đề xuất* dòng skill personal — chưa lưu.
4. Chỉ khi người dùng xác nhận, `accept_suggestion(...)` mới append dòng đó vào
   `knowledge/<role>/personal/<user_id>/SKILL.md` (chỉ chèn vào MODULE 3 guidance).
   Không bao giờ đụng `specs/` (_core).

## Vòng 2 — Đối chiếu khi đóng dự án  (`src/reconcile/close_project.py`)
1. `close(project_id, domain, tech_stack, estimate, actuals, ...)` so estimate với timesheet.
2. Ghi dự án (có actual_md) vào historical DB qua `src/store/historical.append`.
3. Log sai số vào `eval/runs/estimation_accuracy.jsonl`; lệch ngoài ±20% → append risk.
4. Lần sau, `historical.lookup` tìm thấy item này ⇒ Estimation gắn **FACT** thay vì ASSUMPTION,
   và `effort_md` lấy từ median actual thật.

## Vì sao effort là DB-driven
`src/agents/a2_estimation.enrich` không để LLM đặt số. LLM chỉ ra draft WBS; Code tra DB.
Đây là chỗ hai vòng tác động: đóng nhiều dự án ⇒ DB dày ⇒ nhiều item thành FACT ⇒ cost_range
hẹp dần theo `variance_ratios` thật. Kiểm chứng: `tests/contract/test_training_loops.py`.

## Holdout khi eval
`historical.lookup(..., exclude_ids=...)` loại dự án đang test khỏi DB, tránh "tra ra đáp án".

## Vòng 3 — Học qua từng lượt sửa với user (revision_log)
Khác vòng 1 (chỉ ở gate) và vòng 2 (chỉ khi đóng dự án): vòng 3 bắt MỌI lượt user chỉnh output.
1. `src/learning/revision.py` ghi `{before, after, note, kind}` vào Blackboard `revision_log`.
2. Reflection đọc log ngay trong phiên → agent không bắt user sửa cùng lỗi 3 lần.
3. `src/learning/distiller.py` cô đọng pattern lặp ≥3 → `knowledge/<role>/learned/PROPOSED.md` (status proposed).
4. Qua eval regression + người duyệt → promote thành `learned/SKILL.md` (lúc đó skill_loader mới nạp).
Thứ tự nạp 4 lớp: personal → learned → team → _core.
