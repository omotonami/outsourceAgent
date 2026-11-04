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
  MultiFeed(store, since)              chế độ "Tất cả dự án" (Control Room): mỗi agent 1 nhân vật, status gộp +
                                       work [{project, status, kind main|side}], task theo dự án (+ task giao thêm),
                                       event có nhãn project, usage gộp + by_project, projects [{id, state,
                                       escalated, gate}] — GET /api/room/all, /api/room/all/stream
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable

from src.blackboard.proxy import ReadOnlyBoard
from src.blackboard.store import Blackboard
from src.orchestrator import gate_view, room_state, team_feed, usage

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


# ---------- Chế độ "Tất cả dự án" (Control Room) ----------
RANK = {"blocked": 3, "working": 2, "waiting_gate": 1, "idle": 0}
ESCALATED = "ESCALATED"


def merge_usage(parts: dict[str, dict]) -> dict:
    """Gộp usage nhiều dự án (pid → usage dict) + by_project {pid: total}."""
    out = {"tokens_in": 0, "tokens_out": 0, "total": 0, "calls": 0, "by_agent": {}, "by_tier": {},
           "est_cost_usd": 0.0, "budget": None, "by_project": {}}
    for pid, u in parts.items():
        for k in ("tokens_in", "tokens_out", "total", "calls"):
            out[k] += int(u.get(k) or 0)
        out["est_cost_usd"] += float(u.get("est_cost_usd") or 0.0)
        for a, n in (u.get("by_agent") or {}).items():
            out["by_agent"][a] = out["by_agent"].get(a, 0) + int(n)
        for tier, v in (u.get("by_tier") or {}).items():
            d = out["by_tier"].setdefault(tier, {"tokens_in": 0, "tokens_out": 0, "total": 0, "calls": 0})
            for k in d:
                d[k] += int(v.get(k) or 0)
        b = u.get("budget") or {}
        out["by_project"][pid] = {"total": int(u.get("total") or 0), "calls": int(u.get("calls") or 0),
                                  "est_cost_usd": round(float(u.get("est_cost_usd") or 0.0), 6),
                                  "ratio": b.get("ratio")}
    out["by_agent"] = dict(sorted(out["by_agent"].items(), key=lambda kv: -kv[1])[:usage.TOP_AGENTS])
    out["est_cost_usd"] = round(out["est_cost_usd"], 6)
    return out


def merge_agents(meta: list[dict], per: dict[str, list[dict]]) -> list[dict]:
    """Mỗi agent MỘT nhân vật: status gộp (blocked > working > waiting_gate > idle) + `work` theo dự án
    [{project, status, kind, task}] + `main`/`side` = dự án đang làm việc chính / việc phụ."""
    out = []
    for a in meta:
        work, best = [], "idle"
        for pid, agents in per.items():
            x = next((g for g in agents if g["id"] == a["id"]), None)
            if not x or x["status"] == "idle":
                continue
            tid = x.get("current_task")
            work.append({"project": pid, "status": x["status"], "kind": x.get("kind"),
                         "task": f"{pid}:{tid}" if tid else None})
            if RANK.get(x["status"], 0) > RANK[best]:
                best = x["status"]
        act = [w for w in work if w["status"] in ("working", "blocked")]
        main = [w["project"] for w in act if (w["kind"] or "main") == "main"]
        side = [w["project"] for w in act if w["kind"] == "side"]
        cur = next((w for w in act if w["project"] in main), None) or (act[0] if act else None)
        out.append({**a, "status": best, "work": work, "main": main, "side": side,
                    "project": cur["project"] if cur else None, "kind": cur["kind"] if cur else None,
                    "current_task": cur["task"] if cur else None,
                    "tasks": [w["task"] for w in work if w["task"]], "level": None})
    return out


class MultiFeed:
    """Mọi project trong store → MỘT view (cùng giao diện RoomFeed: snapshot / poll / last). seq = seq toàn cục
    của store (AUTOINCREMENT chung mọi project) → Last-Event-ID dùng được như chế độ một project.
    Event: id `<project>|<id>` + field `project`, task_id `<project>:<task>`; task: id `<project>:<id>` + field
    `project` (+ task giao thêm S-…, kind side|main)."""

    def __init__(self, store, since: int | None = None, *, meta: list[dict] | None = None):
        self.store = store
        self.meta = meta if meta is not None else room_state.agent_meta()
        self.feeds: dict[str, RoomFeed] = {}
        self.snaps: dict[str, dict] = {}
        self.last = int(since or 0)
        self._prev = None
        self._since = since
        self._discover(initial=True)
        self._prev = self._key(self._view())

    @staticmethod
    def _key(view: dict):
        return (view["agents"], view["tasks"], view["projects"], view["usage"]["total"])

    def _discover(self, initial=False) -> list[str]:
        new = []
        for p in self.store.projects():
            pid = p["id"]
            if pid in self.feeds:
                continue
            since = self._since if initial else self.last
            f = RoomFeed(self.store, pid, since=since, meta=self.meta)
            self.feeds[pid] = f
            self.snaps[pid] = f._snap(None)
            if initial:
                self.last = max(self.last, f.last)
            new.append(pid)
        return new

    def _project(self, pid: str) -> dict:
        f, s = self.feeds[pid], self.snaps[pid]
        st = (f._view.get("project_status") or {})
        state = st.get("state") or st.get("phase")
        side = room_state.side_tasks(f._view)
        return {"id": pid, "state": state, "escalated": state == ESCALATED,
                "gate": (s.get("gate") or {}).get("id"), "gate_who": (s.get("gate") or {}).get("who"),
                "tokens": int((s.get("usage") or {}).get("total") or 0),
                "side_open": sum(1 for t in side if t["status"] in ("queued", "started")),
                "working": sorted(a["id"] for a in s["agents"] if a["status"] == "working")}

    def _view(self, levels=None) -> dict:
        per = {pid: s["agents"] for pid, s in self.snaps.items()}
        tasks = []
        for pid in sorted(self.feeds):
            for t in self.snaps[pid]["tasks"]:
                tasks.append({**t, "id": f"{pid}:{t['id']}", "project": pid, "kind": "main"})
            for t in room_state.side_tasks(self.feeds[pid]._view):
                tasks.append({"id": t["id"], "title": team_feed._cut(t.get("title") or "", room_state.TITLE_MAX), "status": t["status"],
                              "owner_agent": t["role"], "deps": [], "owns": [], "risk_level": None,
                              "project": pid, "kind": t.get("kind") or "side",
                              "note": (t.get("note") or {}).get("summary"), "reason": t.get("reason")})
        projects = [self._project(pid) for pid in sorted(self.feeds)]
        gates = [{"project": p["id"], "id": p["gate"], "who": p["gate_who"]} for p in projects if p["gate"]]
        agents = merge_agents(self.meta, per)
        if levels:
            for a in agents:
                a["level"] = levels.get(a["id"])
        return {"phases": room_state.PHASES, "statuses": list(room_state.STATUSES), "agents": agents,
                "tasks": tasks, "gate": None, "gates": gates, "projects": projects,
                "escalated": [p["id"] for p in projects if p["escalated"]],
                "usage": merge_usage({pid: s["usage"] for pid, s in self.snaps.items()})}

    @staticmethod
    def _tag(e: dict, pid: str) -> dict:
        """Event của một project → id `<project>|<id>`, task_id `<project>:<task>` (task T1 của 2 dự án khác nhau)."""
        return {**e, "id": f"{pid}|{e['id']}", "project": pid,
                "task_id": f"{pid}:{e['task_id']}" if e.get("task_id") else None}

    def _events(self, limit=None) -> list[dict]:
        evs = []
        for pid, s in self.snaps.items():
            evs += [self._tag(e, pid) for e in s["events"]]
        evs.sort(key=lambda e: (e["ts"] is None, e["ts"] or 0))
        return evs[-int(limit):] if limit else evs

    def snapshot(self, levels=None, limit: int | None = room_state.EVENT_LIMIT) -> dict:
        return {**self._view(levels), "events": self._events(limit), "project": None, "all": True,
                "seq": self.last}

    def poll(self, levels=None, limit: int | None = None) -> list[dict]:
        """Dòng mới của mọi project → tối đa MỘT frame gộp (khi có event mới hoặc view đổi)."""
        self._discover()
        new, ts = [], None
        for pid, f in self.feeds.items():
            rows = self.store.read(pid, after=f.last, limit=limit)
            if not rows:
                continue
            for row in rows:
                f._apply(row)
                ts = max(ts or 0, row.ts)
                self.last = max(self.last, row.seq)
            snap = f._snap(levels)
            self.snaps[pid] = snap
            fresh = [e for e in snap["events"] if e["id"] not in f.seen]
            f.seen.update(e["id"] for e in fresh)
            new += [{**self._tag(e, pid), "seq": rows[-1].seq} for e in fresh]
        view = self._view(levels)
        key = self._key(view)
        if not new and key == self._prev:
            return []
        self._prev = key
        new.sort(key=lambda e: (e["seq"], e["ts"] or 0))
        return [{**view, "seq": self.last, "ts": ts or time.time(), "project": None, "all": True, "events": new}]


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
