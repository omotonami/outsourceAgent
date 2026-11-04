# Phòng 3D (view team agent)

Team agent hiển thị như nhân vật low-poly trong một căn phòng: mỗi agent một bàn theo cụm phase
(A Presales · B Discovery · C Build · D Security · E Ops), đi qua đi lại khi trao đổi. Chỉ đọc.

## Realtime (agent/Dispatcher chạy ở process khác)
```
uvicorn src.server.app:app                 # terminal 1 — dashboard, CHỈ ĐỌC event store
python -m src.demo.live_team --pace 0.8    # terminal 2 — dự án mẫu (huddle + Dispatcher mock, không API key)
```
Mở `http://localhost:8000/room` (mặc định **Tất cả dự án**: `/api/room/all[/stream]`, mỗi agent 1 nhân vật, chip dự án
`!` ESCALATED / `⏳` gate) hoặc `/room?project=<id>`. Control Room `/` nhúng `/room?all=1&embed=1` — xem `docs/control-room.md`.
- **Event store** `src/blackboard/eventstore.py`: SQLite WAL, append-only `(seq, project_id, ts, kind, zone, agent,
  payload_json)`, kind ∈ zone_write/zone_append/event/sign/revoke. Đường dẫn `config/runtime.yaml → eventstore.path`
  (mặc định `data/runtime/events.sqlite3`, gitignore) hoặc env `OA_EVENTSTORE_PATH`. `prune()` = retention gọi tay.
- **Ghi xuyên**: `Blackboard(pid, store=EventStore(...))` (hoặc `eventstore.write_through: true`). Chỉ lượt ghi ĐÃ qua
  kiểm quyền + schema mới vào store; payload bỏ khoá thinking/prompt/messages/transcript/contract.
  `Blackboard.load(pid, store)` dựng lại board (không ghi xuyên). Không cấu hình → hành vi cũ.
- **API (chỉ đọc)**: `GET /api/projects` (live = có ghi trong `live_window_s`, kèm `pricing`) ·
  `GET /api/room?project=` (snapshot + `seq` + `usage`) · `GET /api/room/stream?project=&since=` (SSE, id = seq,
  Last-Event-ID nối lại, heartbeat 15s, poll SQLite `poll_ms`) · `GET /api/room/frames?project=&since=0` (replay).
- **room.html**: dropdown project (● live lên đầu, chấm xanh), badge **LIVE** (SSE; lỗi → poll `/api/room` 2s),
  **REPLAY** (project đã xong hoặc nút ⟲: tua frame từ seq đầu, 1x/2x/4x), **DEMO** (`?demo=1` / không server),
  `?dash=1` = board DASH của dashboard (poll như cũ). Đo được: store → trình duyệt trung bình ~0.2s, tối đa < 0.6s.

## Token đã đốt (góc phải trên canvas)
Gateway `trace.log` → dòng `llm_usage` (kind=event, chỉ agent/tier/model/phase/tokens — không prompt/output) vào store
của project. `usage = {tokens_in, tokens_out, total, calls, by_agent, by_tier, est_cost_usd, budget}`
(`src/orchestrator/usage.py`). Đơn giá ước tính: `config/pricing.yaml`; budget: `config/budgets.yaml →
per_project_tokens` (có số → thanh %, vàng ≥ warn_at_ratio, đỏ ≥ 100%). Bấm thẻ → top agent. Demo: số giả lập.

## Backend snapshot — `src/orchestrator/room_state.py`
`snapshot(board, active, gate, levels, limit)` → `{phases, statuses, agents, tasks, events, gate}`; events = summary
Team feed + `from`/`to`, id `<zone>:<idx>`. Frame realtime: `src/orchestrator/room_stream.py`.

## Hành vi
ASSIGN/RESULT/DECISION → nhân vật đi tới bàn đích, bong bóng summary. Huddle → bàn tròn tới DECISION.
SPAWN → worker "nở" cạnh C1; KILL / xong → mờ dần. BLOCKED → "!" đỏ; chờ gate → ⏳. Three.js 0.170.0 (jsDelivr).

## Test
`tests/contract/test_room_state.py`, `tests/contract/test_eventstore.py` (đa process, SSE, e2e live_team).
