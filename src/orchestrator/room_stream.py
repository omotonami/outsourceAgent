"""Phòng 3D realtime — dựng frame CHỈ ĐỌC từ event store dùng chung (src/blackboard/eventstore.py).

Code thuần, không LLM. Board dựng lại từ log (Blackboard.load / _replay, không ghi xuyên) rồi đi qua
room_state.snapshot (chỉ summary của Team feed, không raw thinking/prompt/contract) qua ReadOnlyBoard.

  RoomFeed(store, project_id, since)   board tới seq `since` (None = tới hiện tại)
    .snapshot(levels, limit)           payload như GET /api/room + {project, seq}
    .poll(levels) → [frame]            dòng mới (seq > last) → frame
  frame = {seq, ts, project, events:[room event + seq], agents, tasks, gate, usage}
          gate = {id, agents, who} khi project_status.state = AWAIT_Gx (src/orchestrator/gate_view.room_gate)
          chỉ sinh khi có event mới hoặc agents/tasks/usage đổi (dòng không ảnh hưởng view bị bỏ qua)
  usage = token đã đốt (src/orchestrator/usage.py) từ dòng llm_usage của trace gateway — chỉ số liệu
  stream(...)                          async generator SSE (id = seq, event: room|snapshot, heartbeat)
  projects(store, now, live_window_s)  danh sách project cho GET /api/projects
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable

from src.blackboard.proxy import ReadOnlyBoard
from src.blackboard.store import Blackboard
from src.orchestrator import gate_view, room_state, usage

FRAMES_MAX = 5000
# project_status.phase/state (tên phase dự án hoặc chữ cái) → phase của phòng
STATE_PHASE = {"presales": "A", "discovery": "B", "build": "C", "security": "D", "ops": "E", "done": "E",
               **{k: k for k in room_state.PHASES}}


class RoomFeed:
    def __init__(self, store, project_id: str, since: int | None = None, *, meta: list[dict] | None = None):
        self.store, self.project_id = store, project_id
        self.meta = meta if meta is not None else room_state.agent_meta()
        self.board = Blackboard(project_id, store=None)
        self.usage = usage.Usage()
        self._price, self._budget = usage.prices(), usage.budget()
        self.last = 0
        for row in store.read(project_id, upto=since):
            self._apply(row)
        if since is not None:
            self.last = max(self.last, int(since))
        self._view = ReadOnlyBoard(self.board, owner="room_stream")
        self.seen = {e["id"] for e in room_state.events(self._view)}
        self._prev = None

    def _apply(self, row) -> None:
        if row.zone == usage.ZONE:
            self.usage.add_record(row.payload)
        else:
            self.board._replay(row)
        self.last = row.seq

    def usage_dict(self) -> dict:
        return self.usage.to_dict(self._price, self._budget)

    def _snap(self, levels=None, limit=None) -> dict:
        gate = gate_view.room_gate(self._view)           # gate đang chờ theo project_status (cùng nguồn store)
        snap = room_state.snapshot(self._view, gate=gate, levels=levels, limit=limit, meta=self.meta)
        if gate and snap["gate"]:
            snap["gate"]["who"] = gate["who"]
        return {**snap, "usage": self.usage_dict()}

    def snapshot(self, levels=None, limit: int | None = room_state.EVENT_LIMIT) -> dict:
        return {**self._snap(levels, limit), "project": self.project_id, "seq": self.last}

    def poll(self, levels=None, limit: int | None = None) -> list[dict]:
        """Đọc dòng seq > last, áp từng dòng, trả frame cho dòng làm view đổi."""
        out = []
        for row in self.store.read(self.project_id, after=self.last, limit=limit):
            self._apply(row)
            snap = self._snap(levels)
            new = [{**e, "seq": row.seq} for e in snap["events"] if e["id"] not in self.seen]
            view = (snap["agents"], snap["tasks"], snap["usage"]["total"])
            if not new and view == self._prev:
                continue
            self._prev = view
            self.seen.update(e["id"] for e in new)
            out.append({"seq": row.seq, "ts": row.ts, "project": self.project_id, "events": new,
                        "agents": snap["agents"], "tasks": snap["tasks"], "gate": snap["gate"],
                        "usage": snap["usage"]})
        return out


def sse(data: dict, event: str = "room", id_: int | None = None) -> str:
    head = (f"id: {id_}\n" if id_ is not None else "") + f"event: {event}\n"
    return head + "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"


async def stream(feed: RoomFeed, *, poll_s: float, heartbeat_s: float, levels=None, initial: bool = False,
                 is_disconnected: Callable[[], Awaitable[bool]] | None = None,
                 sleep=asyncio.sleep, clock=time.monotonic, max_polls: int | None = None):
    """SSE: `retry`, (snapshot nếu initial), rồi frame theo seq; comment heartbeat mỗi heartbeat_s.
    Đọc SQLite trong thread (không chặn event loop). Dừng khi client ngắt (hoặc max_polls — cho test)."""
    yield "retry: 2000\n\n"
    if initial:
        yield sse(feed.snapshot(levels), event="snapshot", id_=feed.last)
    beat, n = clock(), 0
    while max_polls is None or n < max_polls:
        n += 1
        if is_disconnected is not None and await is_disconnected():
            return
        frames = await asyncio.to_thread(feed.poll, levels)
        for f in frames:
            yield sse(f, id_=f["seq"])
            beat = clock()
        if clock() - beat >= heartbeat_s:
            yield ": hb\n\n"
            beat = clock()
        await sleep(poll_s)


def projects(store, now: float | None = None, live_window_s: float = 30) -> list[dict]:
    """[{id, last_ts, first_ts, last_seq, count, phase, phase_name, state, active}] — live trước, mới trước."""
    now = time.time() if now is None else now
    out = []
    for p in store.projects():
        st = p.get("status") or {}
        state = st.get("state") or st.get("phase")
        phase = STATE_PHASE.get(str(state or "").lower()) or STATE_PHASE.get(str(state or "")) \
            or (room_state.phase_of(p["last_agent"]) if p.get("last_agent") else None)
        out.append({"id": p["id"], "first_ts": p["first_ts"], "last_ts": p["last_ts"], "last_seq": p["last_seq"],
                    "count": p["count"], "phase": phase, "phase_name": room_state.PHASES.get(phase),
                    "state": state, "active": (now - p["last_ts"]) <= live_window_s})
    out.sort(key=lambda x: (not x["active"], -x["last_ts"]))
    return out
