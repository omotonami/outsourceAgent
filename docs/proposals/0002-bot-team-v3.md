# Đề án v3 — Thêm "team sống" (Peng & Lauren) vào outsourceAgent

> Trạng thái: **APPROVED (2026-11-03)** · Lộ trình: duyệt · Sửa `specs/` theo mục 4: duyệt ·
> **G3 auto-merge (H4 mức L3): BẢO LƯU** — Autonomy Ladder chỉ làm L0–L2, không gate nào tự ký ·
> Push/PR: xử lý sau khi hoàn thành cải tiến.
> Bổ sung (2026-11-03 19:16): task phụ thuộc tạo worktree từ **nhánh task cha** · thêm `owns` vào output B4 ·
> **bỏ bản sao config**, code đọc thẳng `specs/` (bổ sung `clarify_answers` vào specs) · duyệt writer `orchestrator` cho `trace_log` ·
> **Huddle KHÔNG nối vào state machine** (gọi tay) · sau khi xong 5 đợt: **refactor toàn bộ**.
> Nguyên tắc: **Chat thì sống, output thì cứng.** Giữ nguyên state machine, guard, Blackboard, gateway.
> Chỉ thêm lớp tổ chức bot lên trên.
> Bản ghi quyết định: [ADR 0002](../adr/0002-bot-team-v3.md) · lịch sử triển khai: [docs/history/2026-11-03_v3-bot-team.md](../history/2026-11-03_v3-bot-team.md).

---

## 1. Khác nhau ở đâu

| Nguyên tắc Peng/Lauren | outsourceAgent hiện tại | Kết luận |
|---|---|---|
| 1. Một bot một vai + anti-job | **Đã có, chặt hơn**: Agent Card v5, Forbidden do Code kiểm, Done-when, schema I/O | Giữ. Không cần học |
| 2. Bot tạo bot (Dr. Eggbot) | **Không có.** Thêm 1 agent = sửa tay ~7 chỗ (spec, 2 schema, runner, routing, zone, skill, test) | **Học — đòn bẩy lớn nhất** |
| 3. Mô tả bot quản lý thật chặt | Orchestrator là Code nên không "tự làm" được (tốt). Nhưng **không có ai phát task** từ `task_backlog` xuống nhiều C1 | Học một phần: thêm Dispatcher |
| 4. Group chat để khám phá / phân cấp để thực thi | **Chỉ có phân cấp.** Agent không bao giờ phản biện nhau; Clarify chỉ hỏi người | Học: thêm Huddle cho phase khám phá |
| 5. Gắn công cụ thật + workspace chung | Integrations (repo, jira, figma, confluence) **đều stub**. Không có workspace chung | Học: đây là lý do hệ chưa "chạy thật" |
| 6. Tin dần: thủ công → skill → routine | Có 3 vòng học cho **kiến thức**, nhưng autonomy **cố định**, không có cơ chế nâng | Học: thêm Autonomy Ladder |
| (ngầm) Chỉ nói với Chief of Staff | Người làm việc qua dashboard + từng gate | Học: thêm cửa CoS |

### Mạnh hơn họ (không được đánh mất)
- Orchestrator thuần Code + guard từng transition → không loạn khi thêm bot.
- Blackboard phân quyền theo zone → agent không ghi đè nhau.
- Gateway: routing tier, PII mask, budget, trace, model pin.
- Con số do DB/Code đặt, LLM không bịa effort. FACT/ASSUMPTION/RECOMMENDATION.
- Eval holdout, 3 vòng học có người duyệt, ISO 42001.

### Yếu hơn họ
- Thêm/đổi bot quá nặng → hệ cứng, khó "áp cho sản phẩm khác" (SPEC §10) dù kiến trúc cho phép.
- Không chạy song song: BUILD đi tuần tự dù B4 đã ra DAG task.
- Không có pha "nghĩ cùng nhau" → requirement mơ hồ chỉ được bắt ở gate, muộn.
- Tool stub → chưa có output thật để tích lũy niềm tin.
- Không có lối vào hội thoại → người phải biết hệ để dùng.

### Không học (điểm yếu của họ)
- Cho bot chat tự do trong BUILD/SECURITY.
- Để LLM quyết định luồng điều phối hay chọn model.
- Bot do bot tạo được chạy ngay mà không qua người.
- Merge tự động ở rủi ro cao.

---

## 2. Năm hạng mục cải thiện

### H1. Agent Factory ("Eggbot") — bot tạo bot
**Mục tiêu:** thêm một agent mới trong 1 phiên hội thoại, không sửa tay.

- `src/factory/interview.py` — LLM hỏi ≤ 7 câu: 1 job, anti-jobs, input, output, tools, phase, risk.
- `src/factory/scaffold.py` — **Code** sinh đủ bộ từ template:
  `specs/02_agents/<id>.md` · `specs/04_schemas/<id>.in/out.json` · `src/agents/<id>.py` ·
  entry `specs/03_routing.yaml` · zone `specs/05_blackboard.yaml` · `knowledge/<role>/team/SKILL.md` · `tests/contract/test_<id>.py`
- `src/factory/agent_lint.py` — chặn nếu: thiếu field Card v5, Forbidden rỗng, schema lỗi, zone trùng writer, routing thiếu, không có test.
- Output là **branch/PR**, không ghi thẳng `specs/`. Người duyệt → merge.

**Done when:** sinh agent mẫu `x9_demo` → `agent_lint` pass → `pytest -q` pass; lint bắt được 5 lỗi cố ý.

### H2. Build Dispatcher + chạy song song (spawn có luật)
**Mục tiêu:** `task_backlog` của B4 được phát cho nhiều C1 cùng lúc.

- `src/orchestrator/dispatcher.py` (Code, không LLM): lấy task có `deps` đã xong → spawn C1 worker.
- Spawn Contract cho mỗi worker: `task_id, input_slice, owns[files], output_schema, budget (trừ từ budget phase), ttl`.
- Luật: depth tối đa 1 (worker không spawn) · `max_parallel` trong `config/budgets.yaml` · `owns` không giao nhau · mỗi worker một git worktree.
- Vòng đời mỗi task theo `task_substates` đã có: `ready → in_dev → testing → qc → await_merge → merged`.
- Thêm vào card B4: `subordinates: [c1_dev, c2_tester, c3_qc]` để rõ ai dưới ai (bài học Lauren).
- Prerequisite: `src/tools/integrations/repo.py` chạy thật (clone, worktree, branch, PR).

**Done when:** DAG 6 task / 3 tầng → chạy đúng thứ tự, đúng `max_parallel`, không trùng `owns`, trace có `SPAWN/KILL`.

### H3. Huddle — chế độ khám phá cho PRESALES & DISCOVERY
**Mục tiêu:** bắt mơ hồ trước gate, không phải ở gate.

- Kích hoạt: A1 có `ambiguities` ≥ 3, hoặc B1/B2/B4 phát hiện xung đột story ↔ screen ↔ NFR.
- Vai: Proposer (agent chủ phase) → Critic (agent phase kề) → Decider (agent chủ phase). Tối đa 3 vòng.
- Ghi vào zone mới `huddle_log` (append-only). Kết thúc bắt buộc bằng `decision_record`
  `{question, options[], chosen, rationale, risks[], open_questions[]}` (schema mới trong `specs/04_schemas/`).
- `open_questions` đổ vào Clarify (đã có). G1/G2 hiển thị `decision_record`, không hiển thị transcript.
- Huddle **không được** ghi zone sản phẩm (requirements, estimate…) — chỉ đề xuất.

**Done when:** testset 5 brief mơ hồ → tỉ lệ bị trả lại ở G2 giảm so với baseline (eval/baselines).

### H4. Autonomy Ladder — tin dần theo (agent, task_type)
**Mục tiêu:** biến "autonomy cố định" thành cơ chế có số liệu.

| Mức | Nghĩa |
|---|---|
| L0 Manual | Người kích hoạt từng lần |
| L1 Skill | Chạy theo skill đã promote, người duyệt mọi output |
| L2 Routine | Tự chạy theo trigger, người duyệt ở gate |
| L3 Auto-gate | Gate tự ký nếu điều kiện Code thỏa (chỉ **G3** với `risk_level=low`) |

- Nâng mức: N lần gate liên tiếp duyệt không sửa (đọc từ `gate_feedback.jsonl` + `revision_log`), ngưỡng trong `config/`.
- Hạ mức tự động khi: có revision, regression eval, hoặc incident.
- **G1 (giá) và G4 (release/bảo mật) không bao giờ L3.**
- `gate_ui` ghi chữ ký với `by: "auto-L3"` để audit được.

**Done when:** test nâng/hạ mức; G1/G4 từ chối auto-sign kể cả khi đủ điều kiện.

### H5. Chief of Staff — một cửa cho người
**Mục tiêu:** người chỉ nói với một bot (bài học Peng/Lauren).

- `cos` = mở rộng của `e1_pm`: vẫn **chỉ đọc** + được tạo 2 loại yêu cầu thay người, có xác nhận: `lead_brief` mới, `clarify_answers`.
- Không ký gate, không ghi zone sản phẩm. Gate vẫn qua `gate_ui`.
- Trả lời theo khuôn: Hiểu việc · Cần làm rõ · Kế hoạch · Phân công · Cần quyết định.
- Dashboard thêm tab **Team feed**: dựng từ `trace_log` theo dạng `ASSIGN / SPAWN / RESULT / BLOCKED / DECISION / KILL`.
- Kèm: Figma MCP cho B3 — người làm design system + 1 màn mẫu, B3 nhân ra màn còn lại ("làm 5% đầu").

---

## 3. Lộ trình

| Đợt | Hạng mục | Vì sao thứ tự này |
|---|---|---|
| 1 | **H1 Factory** | Làm xong thì H2–H5 cần agent mới đều sinh bằng Factory → tự kiểm chứng Factory |
| 2 | **repo.py thật + H2 Dispatcher** | Có output thật thì mới có số liệu cho H4 |
| 3 | **H3 Huddle** | Giảm rework ở G2 |
| 4 | **H4 Autonomy Ladder** | Cần dữ liệu gate từ đợt 2–3 |
| 5 | **H5 CoS + Team feed + Figma** | Lớp trải nghiệm, làm khi lõi đã ổn |

Mỗi đợt: test trong `tests/` + `pytest -q` pass + không nới `gates.py` (CLAUDE.md luật 4, 8).

---

## 4. Thay đổi `specs/` cần duyệt

- `01_state_machine.yaml`: không đổi state; thêm `max_parallel` (hoặc để ở `config/budgets.yaml`).
- `02_agents/b4_tech_lead.md`: thêm field `subordinates`.
- `02_agents/`: card mới `cos`, (tùy) `factory_interviewer`.
- `04_schemas/`: `decision_record.json`, `spawn_contract.json`.
- `05_blackboard.yaml`: zone `huddle_log` (append-only, writers: agent tham gia huddle).
- `06_gates.md`: G3 thêm dòng auto-L3 cho `risk_level=low`; G1/G4 ghi rõ "không bao giờ auto".

---

## 5. Rủi ro

| Rủi ro | Chặn bằng |
|---|---|
| Factory sinh agent kém chất lượng | `agent_lint` + PR + người duyệt; agent mới mặc định L0 |
| Song song làm hỏng repo | `owns` không giao nhau + worktree riêng + G3 vẫn chặn merge |
| Huddle tốn token, nói vòng vòng | Tối đa 3 vòng, budget riêng, bắt buộc kết thúc bằng `decision_record` |
| Auto-merge sai | Chỉ G3 + `risk_level=low` + hạ mức tự động khi có lỗi |
| CoS vượt quyền | Chỉ đọc + 2 loại ghi có xác nhận; không có quyền `gate_signatures` |
