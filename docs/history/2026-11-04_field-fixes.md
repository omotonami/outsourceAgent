# 2026-11-04 — Sửa lỗi chạy thực tế ở account công ty (field fixes)

- Nhánh: `feat/v3-bot-team` · từ `59785eb` (481 test) → các commit của đợt (prefix `fix(...)`/`feat(...)`).
- Giao việc: Chief of Staff của chủ repo (Daipc) giao bot "Gỡ" — sửa 2 lỗi BACKEND thấy khi chạy dự án thật,
  model thật ở máy công ty (ảnh chụp từ máy công ty). KHÔNG sửa UI html (`room.html`/`index.html`) — bên công ty
  đã sửa UI, merge sau.

## Lỗi báo về (nguyên văn ý chính từ đề bài giao việc)
| Mã | Hiện tượng |
|---|---|
| A | Presales ESCALATED ngay ở A1: `a1_bu: SCHEMA_FAIL — 'business_context' is a required property [output 4735 ký tự, mở đầu bằng chữ, có '{': True, có ```: False, kết thúc bằng '}': True]`. Sau đó dự án kẹt ESCALATED, người không có cách gỡ. |
| B | Phòng 3D không thấy agent nào "đang làm" ở presales/discovery ("Agent 14 · 0 đang làm"), nhân vật không di chuyển theo bước thật. |

## Ranh giới (đề bài)
Không sửa room.html/index.html; không nới gate/guard; không cho máy gỡ escalate/ký; không auto-ký; không push/PR/remote;
không tự điền danh tính người. Được phép sửa phần MÔ TẢ output trong `specs/02_agents/*.md` cho khớp schema
(sửa lỗi lệch spec↔schema; không đổi luật/Forbidden).

## Mục 1 — runtime parse JSON chịu văn bản/fence + retry nêu khoá gốc (lỗi A)
**Nguyên nhân gốc:**
1. `runtime._extract_json` chỉ bóc code fence khi output BẮT ĐẦU bằng ```; văn bản dẫn trước JSON → `json.loads` lỗi.
2. Model không bao giờ thấy schema output: system prompt chỉ có câu tóm tắt ở MODULE 4. Với A1 tóm tắt
   `{business_context, domain, modules…}` đọc như "cả object là business_context" → model bỏ khoá gốc
   `business_context` (hoặc lồng các khoá khác vào trong nó).
3. Lệch spec↔schema ở nhiều agent: 9 agent (a1, a3, b1, b2, b4, c1, c2, c3, d1) được prompt bảo ghi
   `reflection.issues` trong khi schema `additionalProperties: false` không có `reflection` → model làm đúng
   prompt vẫn SCHEMA_FAIL. MODULE 4 của a2/c1/c2 mô tả khoá khác schema; c3/d1 bị cắt cụt ở dấu `|` (`{verdict: pass`).
4. Lượt retry chỉ nói "Output không hợp lệ: <str(ValidationError)>" — chuỗi dài gồm cả instance (raw output) và
   không nêu rõ khoá gốc nào bắt buộc.

| Đã đổi gì | File chính |
|---|---|
| `_extract_json(text, schema=None)`: quét mọi `{` bằng `json.JSONDecoder.raw_decode`, gom object cấp ngoài; có schema → object khớp schema xuất hiện cuối; không có → object lớn nhất (hoà → cái sau). Không eval; không tự bọc/bỏ bọc khoá gốc. Huddle dùng chung hàm (tương thích) | `src/agents/runtime.py` |
| System prompt = prompt spec + khối `OUTPUT JSON SCHEMA (specs/04_schemas/<id>.out.json)` nguyên văn + danh sách khoá gốc bắt buộc (Code chèn sau Module 4; không phải knowledge, không đụng Module 1/2/4/Forbidden) | `src/agents/runtime.py` |
| Retry: thông báo lỗi = message + vị trí; khoá gốc BẮT BUỘC; thiếu ở cấp gốc; khoá thừa (schema cấm); "phải nằm ở CẤP GỐC, không lồng trong X"; yêu cầu "CHỈ MỘT object JSON… không văn bản trước/sau, không code fence" | `src/agents/runtime.py` |
| Số lượt retry từ `config/runtime.yaml → agent_runtime.schema_retries` (mặc định giữ như cũ = 1); `run(max_retries=None)` đọc config | `config/runtime.yaml`, `src/agents/runtime.py` |
| Lý do SCHEMA_FAIL (vào risk_register khi escalate) = message + hình dạng output (số ký tự, mở đầu bằng chữ, có `{`, có ```, kết thúc `}`) — KHÔNG có raw output; kết quả lỗi thêm `attempts` | `src/agents/runtime.py` |
| Spec (chỉ phần mô tả output, cho KHỚP schema): a1 Card Output + MODULE 4 (nêu rõ 6 khoá gốc anh em, `business_context` là object bắt buộc ở cấp gốc); a2 MODULE 4 (draft WBS `{id,item,role,size,module?}` + `reflection`, không trả effort/cost — Card giữ vì mô tả estimate sau Code enrich); c1/c2 Card + MODULE 4 theo schema; c3/d1 sửa tóm tắt bị cắt cụt; 9 agent không có `reflection` trong schema: câu Reflection đổi thành "fix any failure… schema has NO `reflection` field: do not add it". Forbidden/Done when/luật không đổi | `specs/02_agents/{a1_bu,a2_estimation,a3_proposal,b1_ba,b2_ux,b4_tech_lead,c1_dev,c2_tester,c3_qc,d1_soc}.md` |
| Test cũ `test_factory_interviewer_uses_spec_prompt_and_routing`: so system == prompt → đổi thành system bắt đầu bằng prompt spec + có khối schema | `tests/contract/test_factory.py` |

Test mới (`tests/contract/test_runtime_extract.py`, 18): văn bản + JSON, JSON + văn bản, ```json giữa đoạn, fence
không ngôn ngữ, ngoặc lẻ; ưu tiên object khớp schema; không eval / dict Python bị từ chối; không bọc/bỏ bọc;
thiếu khoá gốc → retry nêu `business_context`; bọc → retry nói CẤP GỐC; khoá thừa `reflection`; reason không chứa
raw output; số retry từ config; system có schema; mọi MODULE 4 có tóm tắt nêu đủ khoá gốc bắt buộc; không spec nào
đòi `reflection.issues` khi schema cấm.

Số test sau mục 1: **499**.

## Mục 2 — NGƯỜI gỡ ESCALATED (lỗi A, phần "kẹt ESCALATED")
Trước đây: `project run` gặp ESCALATED chỉ in "CLI không tự gỡ ESCALATED"; state machine không có đường ra.
Thiết kế theo đề bài: cùng chuẩn với ký gate (luật 10) — chỉ người, 2 kênh (dashboard API / terminal TTY người gõ).

| Đã đổi gì | File chính |
|---|---|
| Lõi `resolve(project_id, action, signer, via, note)`: action `retry` (state về phase lỗi; agent lỗi chạy lại ở `run` kế tiếp với signal `retry_2` → router nâng tier lên `escalate_to` nếu `specs/03_routing.yaml` cho phép), `backtrack:<PRESALES\|DISCOVERY\|BUILD\|SECURITY>` (≤ phase lỗi; `Orchestrator.backtrack` huỷ chữ ký gate phía sau; chạy lại các phase từ đó tới phase lỗi như gate bị trả lại), `abandon` (state `ABANDONED`). Người: `confirm.require_human` (máy/agent/gate_ui/auto-* → từ chối, không ghi gì). `packet_hash` của tình trạng ESCALATED (đổi giữa chừng → từ chối) | `src/ops/escalation.py` |
| Ghi: DECISION `by=orchestrator.escalation` (decision, signer, channel, note, escalated_from, agent/signals hoặc backtrack_to/rerun) vào trace_log; risk_register là append-only → APPEND bản ghi resolution `{resolves: <chỉ số risk escalate>, status: resolved, resolved_by: <người>, via: gate_ui}` (writer gate_ui); `project_status` cập nhật | `src/ops/escalation.py` |
| Consumer risk coi risk đã được trỏ bởi bản ghi resolution là đóng: `team_feed.resolved_indexes/open_risks`; dùng ở room_state, status.compute, gate_view highlights, report, e2_cos, pipeline | `src/orchestrator/{team_feed,room_state,status,gate_view}.py`, `src/ops/{report,pipeline}.py`, `src/agents/e2_cos.py` |
| Pipeline: lúc escalate ghi DECISION `decision=escalate {escalated_from, agent}`; dự án escalate từ bản cũ (không có bản ghi này — như dự án thật ở máy công ty) → suy phase từ lịch sử `project_status` trong event store / tiền tố agent trong lý do. Gọi agent qua `_call` (kw + signal retry; xong → DECISION `retry_done`). Rerun nhận cả DECISION backtrack của escalation. `ABANDONED` → dừng, rc 0. "CẦN NGƯỜI LÀM" khi ESCALATED chỉ lệnh/API gỡ, nói rõ Claude không chạy | `src/ops/pipeline.py`, `src/ops/report.py` |
| State `ABANDONED` (terminal, không transition ra) thêm vào danh sách states — đề bài yêu cầu "trạng thái CLOSED/ABANDONED ghi rõ". Bản spec `specs/01_state_machine.yaml` sửa đồng bộ (chỉ danh sách states). `common.TERMINAL` + `prune --closed-project` nhận ABANDONED. Guard/transition không đổi | `src/orchestrator/transitions.yaml`, `specs/01_state_machine.yaml`, `src/ops/common.py`, `src/ops/prune.py` |
| Terminal: `python -m src.ops.escalation resolve --id <id> --action retry\|backtrack:DISCOVERY\|abandon [--note]` — bắt TTY, in tóm tắt + hash, người gõ tên + `XAC NHAN`; không nhận tên người qua tham số | `src/ops/escalation.py` |
| API: `GET /api/projects/{id}/escalation`, `POST /api/projects/{id}/escalation/{action}` body `signer` + `typed_confirm` (+ `note`, `packet_hash`); mã lỗi như gate (400/404/409). **Không sửa UI html** — đợt UI bên công ty thêm nút gọi API này. Test "endpoint ghi duy nhất" cập nhật: gate + escalation | `src/server/app.py`, `tests/contract/test_eventstore.py` |
| `.claude/settings.json` deny: `python[3] -m src.ops.escalation`, `.venv/bin/python …`, `python src/ops/escalation.py`, `curl/wget/http *api/projects/*/escalation*`, `python -c *src.ops*escalation*` (như gate) | `.claude/settings.json` |
| Skill `flow-new-project`: mục ESCALATED chỉ cách người gỡ, Claude chỉ đưa lệnh | `.claude/skills/flow-new-project/SKILL.md` |

Test mới (`tests/contract/test_ops_escalation.py`, 38): view (lý do/phase/agent/lựa chọn), retry → `run` gọi a1 bằng
tier opus rồi tới AWAIT_G1 + `retry_done`, lần đầu không retry dùng sonnet, retry fail lại → ESCALATED lại, backtrack từ
DISCOVERY về PRESALES huỷ G1 + chạy lại agent dù zone đã có, backtrack quá phase lỗi bị từ chối, abandon → ABANDONED,
máy (11 biến thể) không gỡ được + không ghi gì, action/kênh/hash/project sai, dự án escalate từ bản cũ, CLI TTY (người
gõ / 6 kiểu từ chối không ghi gì / không có cờ tên người / subprocess không TTY), API (GET, retry, backtrack, máy 400,
typed_confirm, action lạ, hash 409), settings deny.

Số test sau mục 2: **537**.

## Mục 3 — pipeline phát sự kiện theo agent (lỗi B)
**Nguyên nhân gốc:** `src/ops/pipeline.py` gọi agent presales/discovery/security trực tiếp, không ghi sự kiện
team nào; chỉ Dispatcher (BUILD) ghi ASSIGN/SPAWN/RESULT theo task. Phòng 3D suy "đang làm" từ task/`active`
→ ở presales/discovery luôn "0 đang làm".

| Đã đổi gì | File chính |
|---|---|
| `pipeline.assign/finish`: trước mỗi lần gọi agent (`Runner._call`, mọi phase presales/discovery/security, kể cả b4) ghi ASSIGN `{from: orchestrator, to: agent, agent, phase, project}` (task_id rỗng = bước phase); sau đó RESULT `{ok: true}` hoặc BLOCKED `{reason: "<agent>: <mã lỗi>: <lý do ≤160 ký tự>"}`; exception (mạng, budget…) → BLOCKED rồi ném tiếp. Không raw output | `src/ops/pipeline.py` |
| Huddle gọi tay (`project huddle`): ASSIGN cho từng thành viên (proposer/critic/decider theo config) trước, RESULT/BLOCKED sau | `src/ops/project.py` |
| `room_state.step_status`: ASSIGN chưa có RESULT/BLOCKED → working; BLOCKED cuối → blocked (tới ASSIGN kế tiếp hoặc DECISION người gỡ ESCALATED retry/backtrack/abandon); RESULT → idle. `snapshot` dùng (ưu tiên giữ nguyên: blocked > waiting_gate > working > idle). Route event: RESULT/BLOCKED của bước phase đi từ agent → orchestrator | `src/orchestrator/room_state.py` |
| Team feed summary cho bước phase: "giao bước presales cho a1_bu", "a1_bu: OK" (trước đây sẽ là "giao None …") | `src/orchestrator/team_feed.py` |

Test mới (`tests/contract/test_pipeline_events.py`, 8): `--mock` presales → ASSIGN/RESULT a1, a2, a3 đúng thứ tự +
trường from/to/phase/project; snapshot giữa chừng (process khác đọc event store khi a2 đang chạy) → a2 working, đúng 1
agent working; discovery b1→b4; agent lỗi → BLOCKED không chứa raw output, người retry → hết blocked, lỗi lại → blocked
(retry_done không xoá); exception → BLOCKED; huddle gọi tay; unit step_status + summary.

Số test sau mục 3: **545**.

## Mục 4 — tài liệu
| Đã đổi gì | File chính |
|---|---|
| Runbook: mục mới "ESCALATED — người gỡ" (3 hành động, 2 đường, mã lỗi API, sau đó `run`); dòng sự cố ESCALATED trỏ tới mục này; thêm dòng "Phòng 3D 0 đang làm"; exit code `abandoned` = 0 | `docs/ops/runbook-daily.md` |
| START_HERE thêm 2 dòng gỡ ESCALATED (55 dòng); README/HANDOFF số test 545 + trỏ file đợt này | `START_HERE.md`, `README.md`, `docs/HANDOFF.md` |

## Mục 5 — đóng gói lại `/home/claude/out/company-kit/`
Bundle `main` + `feat/v3-bot-team` (sau commit cuối của đợt), zip (`git archive`, prefix `outsourceAgent/`, không có
`.git`), `START_HERE.md`, `chief-prompt.md` (bản sao `docs/ops/`). Clone thử từ bundle ra `/tmp/kitcheck3` → pytest +
`history_check --base b7731d3`.

Số test cuối đợt: **545** (481 + 18 runtime + 38 escalation + 8 pipeline events), `agent_lint --all` 14/14,
`history_check --base b7731d3` PASS.

## Việc còn mở
- **UI (đợt bên công ty):** nút gỡ ESCALATED trên dashboard gọi `GET/POST /api/projects/{id}/escalation[/{action}]`
  (body `signer` + `typed_confirm` "XAC NHAN" + `packet_hash`) — đợt này KHÔNG sửa `index.html`/`room.html`.
  Phòng 3D không cần đổi UI: `agents[].status` từ API đã có working/blocked theo bước phase.
- `CLAUDE.md` mục "Vận hành hằng ngày" chưa nhắc `src.ops.escalation` (file thuộc nhóm "ask" — chủ repo quyết có thêm
  1 dòng không). Luật 10 đã bao phủ.
- Retry ở BUILD (Dispatcher) chạy lại Dispatcher nhưng không truyền signal nâng tier cho từng worker.
- Thông điệp lỗi ở lượt retry vẫn chỉ gửi lại khi còn lượt; số lượt (`agent_runtime.schema_retries` = 1) — chủ repo
  quyết có nâng lên 2 theo card ("retry ≤ 2") không.
- Card Output của a2 vẫn mô tả estimate SAU khi Code enrich (khác output LLM ở MODULE 4) — có chủ đích, ghi rõ trong spec.
- Bản ghi resolution trong risk_register có `resolved_by` = tên người do chính người gõ (gate_ui/terminal) — không phải bot điền.
