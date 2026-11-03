# Team feed (H5)

Người xem "team bot đang làm gì" trong một dòng thời gian. Code thuần, chỉ đọc:
`src/orchestrator/team_feed.py` · `GET /api/feed?limit=100` · tab **Team feed** trên dashboard.

## Nguồn → type
| Nguồn (zone) | Điều kiện | type |
|---|---|---|
| `trace_log` | `event` ∈ ASSIGN/SPAWN/RESULT/BLOCKED/KILL (Dispatcher H2) | giữ nguyên |
| `huddle_log` | mỗi lượt nói | RESULT (actor = agent) |
| `decision_records` | mỗi kết quả huddle | DECISION |
| `risk_register` | còn mở và severity High/Critical | BLOCKED |
Event lạ trong `trace_log` (vd gateway) bị bỏ qua. Hiện Dispatcher chưa phát ASSIGN — type để sẵn.

## Event
`{type, ts, actor, task_id, summary, source}` — `summary` dựng từ field whitelist, ≤ 200 ký tự.
**Không bao giờ** trả raw thinking, transcript, prompt, contract hay output đầy đủ.
Sắp theo `ts`; event không có `ts` giữ thứ tự nguồn và xếp sau.

## Cây spawn
Từ `SPAWN`: cha = `parent` hoặc `by` (vd `orchestrator.dispatcher`), con = `task_id`.
Node: `{id, status, attempts, children}`; status = event cuối (OK/FAIL/BLOCKED/KILL).

## Payload `/api/feed`
`{types, events, spawn_tree, counts}`; `limit` kẹp 1–500 (lấy N event mới nhất).
Dashboard: mới nhất ở trên, layout 1 cột khi màn ≤ 760px (mobile).

## Test
`tests/contract/test_team_feed.py` (không có httpx → gọi thẳng handler của route).
CoS (`docs/cos.md`) dùng cùng feed làm `trace_summary`.
