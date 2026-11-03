# SPEC — Outsourcing Multi-Agent Framework

Đặc tả workflow và logic hệ thống, để tra cứu sau. Diagram dùng mermaid (tự vẽ trên GitHub / trình xem Markdown hỗ trợ mermaid).

---

## 1. Tổng quan

Hệ gồm 11 agent chạy qua 4 phase, điều phối bằng một state machine (Code). Agent không chat trực tiếp — phối hợp qua Blackboard (ghi/đọc vùng). Mọi lần gọi model đi qua Gateway. 4 cổng người duyệt chặn mọi thứ ra ngoài. Hệ học qua 3 vòng, bồi vào 4 lớp skill.

Nguyên tắc nền: **Code > Tool > LLM call > Agent**. Mọi output gắn nhãn **FACT / ASSUMPTION / RECOMMENDATION**. Số liệu do Code tính, không do LLM.

---

## 2. Workflow tổng — một task từ lead tới release

```mermaid
flowchart TD
    U["User gửi RFP/brief"] --> IN["Intake (Code): ghi lead_brief"]
    IN --> ORC{"Orchestrator<br/>state machine"}
    ORC --> A["Phase A — Pre-sales<br/>a1 BU · a2 Estimation · a3 Proposal"]
    A --> G1{"G1 · Sales Lead"}
    G1 -->|trả lại| A
    G1 -->|duyệt| B["Phase B — Discovery<br/>b1 BA · b2 UX · b3 UI · b4 Tech Lead"]
    B --> G2{"G2 · Client + PM"}
    G2 -->|trả lại| B
    G2 -->|duyệt| C["Phase C — Build<br/>c1 Dev · c2 Tester · c3 QC"]
    C --> G3{"G3 · Tech Lead"}
    G3 -->|trả lại| C
    G3 -->|duyệt| D["Phase D — Security<br/>d1 SOC"]
    D --> G4{"G4 · TL + bảo mật"}
    G4 -->|trả lại| C
    G4 -->|duyệt| REL["Released"]
    REL --> RC["reconcile → historical DB (vòng 2)"]
    A -.model.-> GW["Gateway: routing · PII · budget · trace"]
    B -.-> GW
    C -.-> GW
    D -.-> GW
    GW --> M["Models: Haiku / Sonnet / Opus"]
```

---

## 3. State machine (logic điều phối)

Orchestrator là Code thuần. Mỗi chuyển state có một guard kiểm tra được bằng máy. Không bước nào do LLM quyết định.

```mermaid
stateDiagram-v2
    [*] --> INTAKE
    INTAKE --> PRESALES: lead_brief_valid
    PRESALES --> AWAIT_G1: presales_ready
    AWAIT_G1 --> DISCOVERY: g1_signed
    DISCOVERY --> AWAIT_G2: discovery_ready
    AWAIT_G2 --> BUILD: g2_signed
    BUILD --> AWAIT_G3: build_ready
    AWAIT_G3 --> SECURITY: g3_signed
    SECURITY --> AWAIT_G4: security_scanned
    AWAIT_G4 --> RELEASED: g4_signed
    RELEASED --> [*]
    AWAIT_G1 --> PRESALES: reject (backtrack)
    AWAIT_G2 --> DISCOVERY: reject
    AWAIT_G3 --> BUILD: reject
    AWAIT_G4 --> BUILD: reject
    BUILD --> ESCALATED: loop>3 / opus fail / over budget
```

| Guard | Điều kiện (Code kiểm) |
|---|---|
| presales_ready | estimate + proposal qua schema, Reflection pass, assumption coverage 100% |
| discovery_ready | ≥95% story qua ac_lint, backlog không vòng |
| build_ready | build/test/lint/scope pass + QC không còn Critical |
| security_scanned | security_report đã ghi |
| g*_signed | có chữ ký gate tương ứng (chỉ người ký) |

Backtrack: gate từ chối → quay về phase trước, huỷ mọi chữ ký gate phía sau.

---

## 4. Giải phẫu một agent (logic bên trong)

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant R as runtime (4 module)
    participant K as skill_loader
    participant G as Gateway
    participant M as Model
    participant B as Blackboard
    O->>R: chạy agent
    R->>R: M2 validate INPUT (schema)
    R->>K: nạp skill: personal→learned→team→_core
    R->>R: Reflection đọc revision_log (học trong phiên)
    R->>G: gọi model (tier theo routing)
    G->>G: PII mask · budget · chọn Haiku/Sonnet/Opus
    G->>M: request
    M-->>G: text
    G->>G: ghi trace (model/token/cost)
    G-->>R: text
    R->>R: M4 validate OUTPUT; lỗi → retry → escalate
    R->>R: post-process (số liệu, coverage) — Code, không LLM
    R->>B: ghi vùng của agent (kiểm quyền)
```

4 module: M1 Identity (vai trò + Forbidden) · M2 Input Contract · M3 Task (+ skill guidance) · M4 Output Contract. Skill 4 lớp chỉ chèn vào M3, không nới M1/M2/M4.

---

## 5. Blackboard — data contract

| Vùng | Ghi bởi | Đọc bởi |
|---|---|---|
| lead_brief | intake | tất cả |
| business_context | a1_bu | a2, a3, b1 |
| estimate | a2_estimation | a3, Sales Lead |
| proposal | a3_proposal | Sales Lead (G1) |
| requirements | b1_ba | b2, b3, b4, c1 |
| design_spec | b2_ux | b3, b4 |
| ui_system | b3_ui | b4, c1 |
| architecture, task_backlog | b4_tech_lead | c1, c3 |
| build_results | c1/c2/c3 | d1, PM |
| security_report | d1_soc | Release gate |
| gate_signatures | chỉ người (gate_ui) | Orchestrator |
| risk_register | mọi agent (append) | PM |
| revision_log | mọi agent (append) | Reflection, distiller |
| trace_log | gateway | PM, Eval |

Schema cứng cho estimate/requirements/ui_system/security_report. Vùng append-only: risk_register, revision_log, trace_log.

---

## 6. Model routing (logic chọn model)

Bảng tra bằng Code theo `(agent, task_type, risk_level)`. Bắt đầu tier mặc định, escalate khi có trigger.

```mermaid
flowchart LR
    T["task"] --> R{"risk_level?"}
    R -->|normal| S["Sonnet (việc chính)"]
    R -->|parse/triage| H["Haiku"]
    R -->|high / quyết định đắt| O["Opus"]
    S -->|schema fail x2 / reflection fail / variance>30% / retry2| O
    O -->|vẫn fail| ESC["escalate người"]
```

Reviewer (QC/Reflection) không yếu hơn generator. Pin version trong `config/models.lock`; đổi version phải chạy lại eval.

---

## 7. Ba vòng học (logic cải thiện)

```mermaid
flowchart TD
    subgraph V3["Vòng 3 — trong lúc làm"]
      ED["user sửa output"] --> RL["revision_log"]
      RL --> REF["Reflection đọc (học ngay trong phiên)"]
    end
    subgraph V1["Vòng 1 — ở gate"]
      GT["người sửa ở G1-G4"] --> FB["gate_feedback"]
    end
    subgraph V2["Vòng 2 — đóng dự án"]
      CL["đóng dự án"] --> HD["historical DB (+actual)"]
      HD --> FACT["lần sau item → FACT"]
    end
    RL --> DIS["distiller: pattern lặp ≥3"]
    FB --> DIS
    DIS --> PRO["learned/PROPOSED.md"]
    PRO --> EV{"eval regression + người duyệt"}
    EV -->|tốt lên| LRN["learned/SKILL.md (được nạp)"]
    EV -->|không| X["bỏ"]
```

Phân biệt `kind`: 'edit' (agent sai → học) vs 'preference' (user đổi ý → nhiễu). Skill học được không tự vào prompt — phải qua eval + duyệt.

---

## 8. Human gates

| Gate | Ai duyệt | Chặn gì | Màn hình hiện trước |
|---|---|---|---|
| G1 | Sales Lead | Gửi giá/proposal ra client | ASSUMPTION, WBS lệch >30%, exclusions |
| G2 | Client + PM | Bắt đầu build | scope_delta, open_questions |
| G3 | Tech Lead | Merge | Important còn mở, file ngoài scope |
| G4 | TL + bảo mật | Release | finding High/Critical, QC Critical |

Chữ ký chỉ ghi qua gate_ui. Từ chối → backtrack.

---

## 9. ISO 42001/42005 (governance, tự động)

`src/iso/`: inventory (đăng ký hệ AI) · impact (triage + nháp đánh giá tác động) · conformance (soát bằng chứng → gaps). Deterministic. Là "conformance-ready", không phải chứng chỉ.

---

## 10. Áp cho sản phẩm khác

Giữ `src/` (hạ tầng). Đổi `specs/02_agents/`, `specs/04_schemas/`, `specs/01_state_machine.yaml`, `specs/06_gates.md`, nạp `data/`. Theo 12 bước IDEA → TASK (xem `docs/framework.md`).

---

## 11. PM — quản lý tiến độ (người + máy)

Không có agent PM quyết định. Chia hai phần:
- **Máy (Orchestrator, Code):** tự ghi vùng `project_status` từ Blackboard — phase, % task, gate đã ký, budget, open_risk, open_question quá hạn, build loop. Deterministic, không phán đoán. (`src/orchestrator/status.py`)
- **PM agent (e1_pm, CHỈ ĐỌC):** tổng hợp `project_status` + `risk_register` thành báo cáo + cảnh báo (budget>80%, risk mở, question quá hạn, loop cao) cho **PM người**. KHÔNG ghi, KHÔNG quyết, KHÔNG ký gate. (`src/agents/e1_pm.py`)
- **PM người:** nhận báo cáo, quyết ưu tiên/scope, thương lượng client, ký G2, xử lý escalate, chịu trách nhiệm (RACI).

Dashboard gọi `/api/pm` để hiện bức tranh tiến độ cho PM người.
