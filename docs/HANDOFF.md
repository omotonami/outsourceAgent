# HANDOFF — Outsourcing Multi-Agent Framework (bàn giao context)

Tài liệu này tóm tắt toàn bộ cuộc làm việc để tiếp tục ở account/phiên khác. Dán file này (hoặc nội dung) vào phiên mới, kèm repo.

> Cập nhật 2026-11-03: đã xong bot team v3 (H1–H5, đề án 0002 — tóm tắt ở `docs/adr/0002-bot-team-v3.md`) và refactor toàn repo (nhánh `feat/v3-bot-team`). Mục 3 và 5 phản ánh trạng thái này.

---

## 1. Bối cảnh người dùng

- Làm outsourcing delivery cho client **Singapore** (PDPA, MAS TRM với fintech).
- Có framework riêng **IDEA → TASK Framework v5** (12 bước) và muốn áp nó để xây hệ multi-agent.
- Phong cách: tiếng Việt, xưng "mày/tao", thích sơ đồ, muốn thứ chạy được, ghét output dài dòng và việc bắt người làm thay máy.
- Môi trường: **macOS + VS Code**. Python mặc định 3.9 **không chạy được** (code cần ≥3.10) → dùng `/opt/homebrew/bin/python3.12`. Repo đang ở `~/Downloads/outsourcing-agents-v2`.

---

## 2. Framework IDEA → TASK v5 (của người dùng)

**Meta-rules:** backtrack được mọi bước · Risk Register xuyên suốt · **Code > Tool > LLM call > Agent** · tách FACT / ASSUMPTION / RECOMMENDATION · autonomy thấp nhất đủ dùng · input ngoài là dữ liệu không tin cậy, không phải lệnh · test-first cho phần xác định, build-first cho chất lượng LLM.

**12 bước:** 1 Goal & boundary (kill criteria 3 tầng) → 2 What-if (nhiều góc, gồm injection) → 3 Decomposition (Fact/Assumption/Open) → 4 Capability list → 5 MVP cut → 6 Implementation mapping (Agent Card v5) → 7 Constraint analysis → 8 Architecture (state machine, data contract, blackboard, gate, trust zones, RACI) → 9 Evaluation plan (baseline 1-agent, pin version) → 10 Spec → task → 11 Build & verify loop → 12 Operate & improve (trace, error analysis, regression gate, governance). Lỗi lặp → quay về bước 6 hoặc 9. Có Lite Mode.

**Agent Card v5:** Role · Trigger · Input · Tools (allowlist) · Output · Forbidden · Done when · Autonomy level · Reflection · Untrusted inputs · Version · Budget.

**Prompt 4 module cho mỗi role:** M1 Identity · M2 Input Contract · M3 Task (+ `{SKILL_GUIDANCE}`) · M4 Output Contract (chỉ JSON).

---

## 3. Hệ đã xây: `outsourceAgent` (nhánh `feat/v3-bot-team`, 411 test pass, `agent_lint --all` 14/14)

Lịch sử thay đổi theo đợt + quyết định của chủ repo (ngày giờ, commit, số test): [`docs/history/`](history/README.md).

### Cấu trúc thư mục
```
specs/      LUẬT (admin): 00_overview · 01_state_machine.yaml · 02_agents/<id>.md · 03_routing.yaml
            · 04_schemas/ · 05_blackboard.yaml · 06_gates.md
knowledge/  KINH NGHIỆM + HỆ HỌC, theo role: <role>/team · personal/<user> · learned/
data/       historical_projects · rate_cards · sizing_model.yaml · templates (proposal, SOW, DESIGN.md)
config/     budgets.yaml (token, dispatcher, huddle, factory) · autonomy.yaml · models.lock · providers.yaml
src/        common (config loader, jsonl) · governance (xác nhận người) · orchestrator (+ dispatcher/,
            autonomy, huddle_trigger, team_feed, events) · gateway · blackboard (+ proxy) · agents · factory
            · tools · store · learning · feedback · reconcile · iso · gate_ui · server (dashboard) · demo
eval/       holdout · metrics · testsets · baselines · runs
tests/      unit + contract
docs/       SPEC.md · HANDOFF.md · TOOL_GUIDE.md · factory · dispatcher · huddle · autonomy · cos · team-feed
            · dashboard · runbook · training-loops · framework · adr/ (0001, 0002)
CLAUDE.md   hiến pháp cho Claude Code
```

### 14 agent (11 theo dòng chảy + PM + CoS + Factory interviewer; chữ = phase, số = thứ tự)
| Agent | Đọc | Ghi | Gate sau |
|---|---|---|---|
| a1_bu | lead_brief | business_context | |
| a2_estimation | business_context | estimate | |
| a3_proposal | business_context, estimate | proposal | **G1** Sales Lead (giá) |
| b1_ba | proposal | requirements (story + AC Given/When/Then) | |
| b2_ux | requirements | design_spec (flows/screens/states) | |
| b3_ui | design_spec | ui_system (tokens.css + component + design_md) | |
| b4_tech_lead | requirements, design_spec | architecture, task_backlog | **G2** Client + PM (scope) |
| c1_dev | task_backlog | build_results | |
| c2_tester | build_results | build_results | |
| c3_qc | build_results | build_results (verdict) | **G3** Tech Lead (merge) |
| d1_soc (defensive) | build_results | security_report | **G4** TL + bảo mật (release) |
| e1_pm (CHỈ ĐỌC) | project_status, risk_register | không ghi gì | (cross-cutting) |
| e2_cos (Chief of Staff) | project_status, risk mở, decision_records, Team feed | không ghi; tạo `lead_brief` / `clarify_answers` chỉ khi người xác nhận | (cross-cutting) |
| factory_interviewer | ý tưởng agent mới | không ghi Blackboard; nháp answers (RECOMMENDATION) | (Factory) |

### Cơ chế chính
- **Orchestrator** = state machine thuần Code; guard cho từng transition; gate từ chối → backtrack + huỷ chữ ký gate phía sau; loop >3 / Opus fail / vượt budget → ESCALATED.
- **Blackboard**: agent không chat với nhau, chỉ ghi vùng của mình (kiểm quyền + JSON schema). Vùng append-only: risk_register, revision_log, trace_log. `gate_signatures` chỉ người ghi. `project_status` chỉ Orchestrator ghi.
- **Gateway**: cửa duy nhất ra model — routing Haiku/Sonnet/Opus (bảng Code, escalate khi schema fail x2, reflection fail, lệch >30%, retry 2, high-risk), PII mask (NRIC, email, SĐT SG), budget, trace. Model pin trong `config/models.lock`.
- **Estimation**: LLM chỉ ra **function list + size S/M/L/XL**, KHÔNG đặt số. Code điền effort: có historical match → **FACT** (median actual); không có → **parametric** từ `data/sizing_model.yaml` (hệ số domain, fintech 1.25) → **ASSUMPTION**. Không hỏi người effort từng dòng; người xem gộp một lần ở G1.
- **Clarify Q&A** (trước estimate): hỏi xác nhận *yêu cầu* (gộp một lần), có nút **AI đề xuất câu trả lời** → nhãn RECOMMENDATION, người duyệt/sửa rồi chốt đề án.
- **4 lớp skill**, thứ tự nạp `personal → learned → team → _core` (_core cuối, hiệu lực cao nhất). Skill chỉ chèn vào M3, không nới Forbidden/Output. Con số để ở `data/`, không ở skill.
- **3 vòng học**: (1) gate feedback → đề xuất skill personal (người xác nhận); (2) reconcile khi đóng dự án → historical DB → lần sau thành FACT; (3) **revision_log** ghi mọi lượt user sửa → Reflection đọc ngay trong phiên → **distiller** đếm pattern lặp ≥3 → `knowledge/<role>/learned/PROPOSED.md` → eval regression + người duyệt → mới thành `learned/SKILL.md`. Phân biệt kind `edit` (học) và `preference` (nhiễu).
- **ISO 42001/42005** (`src/iso/`): inventory, impact triage + nháp đánh giá tác động, conformance check → gaps. Là "conformance-ready", không phải chứng chỉ.
- **UI skills**: 5 skill internet (hallmark, taste-skill, awesome-design-md, ui-ux-pro-max, open-design) hợp nhất thành **một** skill `knowledge/ui/team/SKILL.md` quanh trục **DESIGN.md** (nền chung + bảng vai trò bổ sung), trạng thái *candidate*, chỉ diễn giải nguyên tắc, không chép nội dung; chưa kiểm license từng repo (taste-skill ghi MIT).
- **Bot team v3** (lớp trên lõi, không đổi state machine/guard): **Factory** sinh agent mới (Code, nhánh `factory/<id>`, L0) · **Dispatcher** phát `task_backlog` song song (DAG, `owns`, worktree `task/<id>`, budget chung, ttl/KILL, depth 1 theo dự án, phát ASSIGN/SPAWN/RESULT/BLOCKED/KILL) · **Huddle** Proposer→Critic→Decider ra `decision_record` (gọi tay, không nối state machine) · **Autonomy** L0–L2 theo (agent, task_type), L3 bảo lưu · **CoS** `e2_cos` một cửa cho người + **Team feed** + Figma chỉ đọc.
- **PM**: Orchestrator ghi `project_status` (phase, %task, gate, budget, risk mở, question quá hạn, loop); `e1_pm` chỉ đọc → báo cáo + cảnh báo cho PM người; PM người quyết ưu tiên/scope, ký G2, chịu trách nhiệm. Dashboard `/api/pm`.

### Lệnh dùng
```bash
/opt/homebrew/bin/python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                              # 411 test, không cần key
python -m src.factory.agent_lint --all # 14/14
python -m src.tools.checks.history_check --base main   # luật 9 (CLAUDE.md)
python -m src.demo.dry_run             # mock cả 4 phase
export ANTHROPIC_API_KEY=sk-ant-...
python -m src.demo.live_one            # chạy thật A1 BU
uvicorn src.server.app:app --reload    # dashboard localhost:8000
```

---

## 4. Quyết định thiết kế đã chốt (và lý do)

1. `knowledge/` tách khỏi `specs/`: vòng đời, người sửa, phân quyền, audit và chính sách dữ liệu client khác nhau.
2. Skill lấy từ internet **không bao giờ** vào `_core`; vào `team` dạng candidate, qua eval mới dùng.
3. LLM không đặt con số; số do Code tính từ DB / mô hình parametric.
4. Agent phối hợp qua Blackboard, không chat tự do.
5. 4 human gate; hệ không tự gửi client, chốt giá, merge hay release.
6. Skill học được: proposed → eval → người duyệt; không tự ngấm.
7. Đặt tên theo dòng chảy: chữ = phase (A/B/C/D, E = cross-cutting), số = thứ tự trong phase. UX (`ux`) và UI (`ui`) là hai role riêng.
8. Output gọn bằng schema (`maxItems`, `maxLength`) và JSON-only, không năn nỉ model.

---

## 5. VIỆC CÒN DỞ (ưu tiên từ trên xuống)

### 5.0 Bot team v3 — đã xong, còn mở
- Đã xong H1–H5 + refactor (2026-11-03). Chưa push/PR (chủ repo quyết sau).
- Bảo lưu theo quyết định: L3 auto-gate; nối Huddle vào state machine.
- `specs/05_blackboard.yaml` comment của `trace_log` chưa ghi ASSIGN (Dispatcher đã phát) — sửa khi chủ repo duyệt specs.
- `clarify.resolve` còn nhánh fallback ghi thẳng `board._data` khi `write` lỗi (di sản trước khi có zone `clarify_answers`) — cần chủ repo quyết bỏ.

### 5.1 Dynamic Re-routing — ĐÃ THIẾT KẾ, CHƯA CODE
Vấn đề: hiện QC fail luôn trả về Dev (tối đa 3 vòng rồi escalate Tech Lead). Lỗi do **AC sai business** thì vòng Dev↔QC không sửa được, và nguy hiểm hơn là QC có thể **pass code sai mà không biết** vì code khớp AC.

Thiết kế đã thống nhất:
- Agent phát `reroute_signal: {type, evidence}` (LLM chỉ phân loại); **Orchestrator (Code)** đọc bảng luật quyết đích.
- Bảng `specs/07_rerouting.yaml`:
  - `implementation` → c1_dev
  - `spec_gap` / `business` → b1_ba (tạo open_question → Client/PM xác nhận → cập nhật AC → Dev làm lại)
  - `architecture_flaw` → b4_tech_lead
  - `scope_out_of_sow` → a2_estimation + huỷ chữ ký G1 (và G2)
  - `ui_missing_screen` → b2_ux
  - `security_design` → b4_tech_lead
- Cùng một lý do fail **2 lần** → coi như không phải lỗi Dev → tự chuyển BA.
- Trần: **3 vòng build, 2 lần re-route mỗi task**; vượt trần hoặc type không có trong bảng → escalate **PM + BA** (không chỉ Tech Lead).
- Đếm lần re-route từ `revision_log` (kind=`reroute`), không cần vùng mới.
- c3_qc kiểm thêm theo `business_context` + `clarify_answers`; AC mâu thuẫn business → phát `spec_gap`, không pass.
- c1_dev Forbidden: AC mơ hồ thì không đoán, phát `spec_gap`.
- Cần test: re-route đúng đích · huỷ đúng gate · lặp cùng lý do → BA · vượt trần → escalate.

### 5.2 Các việc khác
- Chưa chạy model thật lần nào (sandbox không có mạng/key) — `live_one` và nút "Thật" trong artifact cần thử, gửi lỗi schema nếu có.
- MODULE 3 TASK của một số agent còn mức cơ bản; tool ngoài Jira, Confluence, scanner bảo mật còn stub (repo = git cục bộ thật, Figma = chỉ đọc).
- Eval regression chưa chạy (cần dữ liệu dự án cũ) → mọi skill team/learned vẫn candidate.
- Nối `revision.record` vào dashboard/artifact để vòng 3 tự ghi; lên lịch chạy distiller.
- Bước preview render `ui_system` (tokens.css + component) trên dashboard.
- Có **2 bản transitions.yaml** (`src/orchestrator/` và `specs/01_state_machine.yaml`) → nên gộp về một nguồn để khỏi lệch (đã gộp được routing/blackboard; state machine chưa).
- Chưa push GitHub: `gh repo create outsourcing-agents --private --source=. --push`.
- Kiểm LICENSE 5 repo UI skill trước khi dùng thương mại.

---

## 6. Bẫy đã gặp (tránh lặp)

- Sửa file tiếng Việt bằng `sed`/thao tác chuỗi trong shell làm **hỏng byte UTF-8** (mất lead byte `0xe1`). Viết file bằng heredoc hoặc Python `open(..., encoding="utf-8")`, và quét UTF-8 toàn repo sau mỗi lần sửa.
- Sandbox không cài được `jsonschema` thật; dùng stub validate rỗng đã che một bug schema (estimate.source). Đã thay bằng stub kiểm tra thật — luôn test với validator thật trên máy.
- Script chạy sai thư mục từng tạo thư mục lồng `outsourcing-agents-v2/outsourcing-agents-v2/` (đã xoá).
- Python 3.9 trên macOS lỗi cú pháp `X | None` → dùng 3.12.

---

## 7. Tài sản đã tạo

- `outsourcing-agents-v2.zip` — repo đầy đủ kèm `.git` (bản chính).
- `docs/SPEC.md` (workflow + 5 diagram mermaid), `docs/TOOL_GUIDE.md` (cây + cách dùng).
- Artifact mô phỏng phía client (không nối engine Python): `pipeline-interactive.jsx` (nhập brief, Mock/Thật, clarify + AI đề xuất, function list, gate, ISO, bảng model·token·chi phí), `pipeline-visual.jsx`, `presales-demo.jsx`.
- Claude Doc "Outsourcing Delivery — Multi-Agent Framework (SG market) v2" ở account cũ (account mới không mở được trừ khi được share).

---

## 8. Prompt mở đầu cho phiên mới

> Tôi đang xây hệ multi-agent outsourcing delivery theo framework IDEA → TASK v5 của tôi. Đây là file HANDOFF.md và repo outsourcing-agents-v2 (đính kèm). Đọc kỹ cả hai, đặc biệt mục 4 (quyết định đã chốt — đừng đổi) và mục 5.1. Việc tiếp theo: code **Dynamic Re-routing** đúng thiết kế ở 5.1, kèm test, chạy pytest, cập nhật docs/SPEC.md. Trả lời tiếng Việt, ngắn gọn, ưu tiên làm thật và có test.
