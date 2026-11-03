# Phòng 3D (view team agent)

Team agent hiển thị như nhân vật low-poly trong một căn phòng: mỗi agent một bàn theo cụm phase
(A Presales · B Discovery · C Build · D Security · E Ops), đi qua đi lại khi trao đổi. Chỉ đọc.

## Mở
- Server: `uvicorn src.server.app:app` → `http://localhost:8000/room` (hoặc tab **Phòng 3D** trên dashboard).
  Badge **LIVE**: poll `GET /api/room` mỗi 2s, chỉ animate event mới.
- Không server (mở file trực tiếp) hoặc `/room?demo=1` → badge **DEMO**: kịch bản mẫu chạy lặp
  (presales → G1 → huddle B1/B2 → B4 chia 5 task → 3 worker C1 song song → T3 BLOCKED rồi retry,
  T2 QC fail → KILL → retry → G3 chờ người). Nút Pause, 1x/2x/4x, ⌂ về góc nhìn mặc định.
- Three.js `0.170.0` nạp từ jsDelivr qua importmap (cần mạng).

## Backend — `src/orchestrator/room_state.py` (Code thuần)
`snapshot(board, active=(), gate=None, levels=None, limit=200)` →
`{phases, statuses, agents, tasks, events, gate}`.
- agents: 14 spec + `status` ∈ idle/working/blocked/waiting_gate (ưu tiên blocked > waiting_gate > working),
  `current_task`, `level` (L0–L2 từ Autonomy nếu đọc được).
- tasks: từ `task_backlog` (`title` ≤ 80 ký tự, `owner_agent` theo ASSIGN / substate: testing → c2, qc → c3).
- events: summary của Team feed (`team_feed.py`) + `from`/`to`/`parent`/`agent`, `id` = `<zone>:<idx>`.
  Không raw thinking/prompt/contract. Không ghi Blackboard.
- `/api/room`: `active` = agent của bước dashboard hiện tại; `gate` khi bước là gate (G1–G4).

## Hành vi
ASSIGN/RESULT/DECISION → nhân vật nguồn đi theo lối đi tới bàn đích, bong bóng summary, dòng panel sáng lên,
rồi về bàn. Huddle → ngồi bàn tròn tới khi có DECISION. SPAWN → worker "nở" cạnh bàn C1; KILL / task xong
→ mờ dần. BLOCKED → "!" đỏ; chờ gate → ⏳. Bấm nhân vật / dòng agent → camera focus + lọc panel.
`prefers-reduced-motion` → bỏ nhún/gõ phím, di chuyển tức thì. Mobile ≤760px: canvas trên, panel dưới.

## Test
`tests/contract/test_room_state.py` (gọi thẳng handler route như test_team_feed).
