# Agent Factory (H1) — tạo agent mới không sửa tay

Đề án: `docs/proposals/0002-bot-team-v3.md` mục H1. LLM chỉ **nháp**; mọi file do **Code** sinh.

## Luồng
1. **Phỏng vấn** (`src/factory/interview.py`, ≤ 7 câu `QUESTIONS`): id/name · job + done_when ·
   anti_jobs/forbidden · input (+untrusted) · output + zone · tools · phase/trigger/risk/model tier.
   - Tuỳ chọn: `draft_from_llm(idea)` → bản nháp nhãn `RECOMMENDATION`, `confirmed=False`
     (logic LLM ở `src/agents/factory_interviewer.py`, đi qua gateway). Người sửa rồi `confirm()`.
   - `confirm(..., confirmed_by=<người>)` dùng `src/governance/confirm.py` (danh sách máy chung).
   - `build_spec(answers)` kiểm: id dạng `<chữ><số>_<role>`, ≥1 anti_job, phase ∈ `config/budgets.yaml`.
2. **Scaffold** (`src/factory/scaffold.py` — kế hoạch/ghi/rollback; nội dung ở `templates.py`, chèn YAML ở
   `yaml_edit.py`; không LLM) sinh:
   `specs/02_agents/<id>.md` (Card v5 + prompt 4 module, `subordinates`, `anti_jobs`) ·
   `specs/04_schemas/<id>.in/out.json` · `src/agents/<id>.py` · `knowledge/<role>/team|personal/_template` ·
   `tests/contract/test_<id>.py`; chèn entry vào `specs/03_routing.yaml`, zone vào `specs/05_blackboard.yaml`
   (giữ comment, parse lại để kiểm). Runtime (`router.py`, `blackboard/store.py`) đọc thẳng 2 file này — không còn bản sao.
   Mặc định dry-run; **không ghi đè** file/entry/zone/role đã có (`ScaffoldError`, không ghi dở dang).
3. **Lint** (`src/factory/agent_lint.py`): Card v5 đủ field, Forbidden/Done when không rỗng, prompt có
   MODULE 1–4 + `{SKILL_GUIDANCE}`, role = hậu tố id và duy nhất, schema JSON hợp lệ, có entry routing,
   zone owner_write không trùng writer agent khác, có runner, có test; SOURCE: không còn bản sao config
   trong `src/` và runtime đọc literal `specs/03_routing.yaml` / `specs/05_blackboard.yaml`.

## Lệnh
```bash
python -m src.factory.new --spec my_agent.yaml --confirmed-by <người>          # dry-run: liệt kê file sẽ ghi
python -m src.factory.new --spec my_agent.yaml --write --create-branch          # người chạy trong terminal
                                                      # → hiện tóm tắt card + answers_hash
                                                      # → người gõ tên + gõ lại "XAC NHAN"
                                                      # ghi trên nhánh factory/<id> (tự tạo/chuyển) + agent_lint
                                                      # → PR → người duyệt (không merge thẳng, không push)
python -m src.factory.agent_lint <id>                 # hoặc --all
python3 -m pytest -q
```
`my_agent.yaml`: các key như `ANSWERS` trong `tests/contract/test_factory.py`.

## Xác nhận người khi ghi (CLAUDE.md luật 10)
- `--write` **không nhận** `--confirmed-by` (có → từ chối, rc 2). Danh tính người chỉ vào qua terminal tương tác.
- Bắt buộc `sys.stdin.isatty()`; không TTY (bot, pipe, CI, subprocess) → từ chối với thông báo rõ, không ghi gì.
- Trước khi hỏi, CLI kiểm answers hợp lệ (`interview.preview_spec`) và hiện tóm tắt card + `answers_hash`.
  Người gõ tên (phải là người theo `src/governance/confirm.py`) và gõ lại đúng `XAC NHAN`; sai/EOF → huỷ.
- `--spec` là bản ghi đã xác nhận trước đó: kiểm hash rồi **vẫn phải xác nhận lại** — `confirmed_by` trong file
  có thể do bot điền nên không được tin.
- `--confirmed-by` chỉ còn cho dry-run (không ghi gì). API Python `interview.confirm()` giữ cho test và kênh
  đã xác thực người; bot không được gọi nó với danh tính người tự điền.
- Tiền lệ: card `e2_cos` (`fa4b226`) sinh với `--confirmed-by daipc` do bot điền; chủ repo duyệt sau
  (2026-11-04 05:27) — xem `docs/history/2026-11-04_room-3d-realtime.md`.

## Luật
- Agent mới mặc định autonomy **L0**; không chạy thật trước khi PR được duyệt.
- Không dùng Factory để sửa agent đã có. `ALLOWLIST` trong agent_lint chỉ cho ngoại lệ có lý do
  (hiện: `build_results` dùng chung c1/c2/c3 theo thiết kế).
