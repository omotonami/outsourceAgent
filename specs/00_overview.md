# Overview

Multi-agent framework cho outsourcing delivery (SG). Đặc tả đầy đủ: Claude Doc v2.

## Quy ước thư mục
- `specs/` — con người viết. Nguồn sự thật: state machine, agent (card + prompt), schema, routing, gate.
- `knowledge/` — người dùng thêm kinh nghiệm (team/personal). KHÔNG được nới lỏng specs.
- `src/` — Claude Code sinh. Code deterministic + agent runner.
- `data/`, `eval/`, `tests/`, `gate_ui/` như tên gọi.

## Nguyên tắc
Code > Tool > LLM call > Agent. Chỉ `src/agents/` chứa logic LLM; mọi thứ khác deterministic.
Mọi lần gọi model đi qua `src/gateway/`. Gate G1–G4 enforce trong `src/orchestrator/`.
