"""Phòng 3D — snapshot CHỈ ĐỌC cho view `/room`: agent (trạng thái), task, event trao đổi.

Code thuần, không LLM, không ghi Blackboard. Event tái dùng summary của Team feed (team_feed.py) —
chỉ summary ≤ MAX_SUMMARY, không raw thinking/transcript/prompt/contract — rồi gắn thêm `from`/`to`
để view biết nhân vật nào đi tới bàn ai.

  agents  {id, name, role, phase, status, current_task, tasks, level}
          status ∈ STATUSES, ưu tiên: blocked > waiting_gate > working > idle
  tasks   {id, title, status, owner_agent, deps, owns, risk_level}
  events  {id, ts, type, from, to, summary, task_id, parent, agent, source}
  gate    {id, agents} khi đang chờ người ký (do caller truyền), không thì None

Chủ task (owner_agent): agent được ASSIGN (trace_log) hoặc field `agent` của task; theo substate
worker (c1_dev → c2_tester → c3_qc, xem dispatcher/worker.py) thì testing → c2_tester, qc → c3_qc.
"""
from __future__ import annotations

from src.common import config
from src.orchestrator import team_feed
from src.orchestrator.dispatcher.settings import BLOCKED, DEFAULT_AGENT, PARENT, WORKER_STATES
from src.orchestrator.events import EventType

STATUSES = ("idle", "working", "blocked", "waiting_gate")
PHASES = {"A": "Presales", "B": "Discovery", "C": "Build", "D": "Security", "E": "Ops"}
SUBSTATE_AGENT = {"testing": "c2_tester", "qc": "c3_qc"}   # in_dev → agent được giao (mặc định c1_dev)
HUDDLE = "huddle"
TITLE_MAX = 80
EVENT_LIMIT = 200


def phase_of(agent_id: str) -> str:
    """a1_bu → A … e2_cos → E; agent không theo tiền tố phase (vd factory_interviewer) → E (Ops)."""
    p = agent_id[:1].upper()
    return p if p in PHASES and agent_id[1:2].isdigit() else "E"


def _agent_meta(root=None) -> list[dict]:
    out = []
    for aid in sorted(config.agent_ids(root)):
        try:
            meta = config.agent_spec_meta(aid, root)
        except Exception:                     # spec hỏng không làm sập view chỉ đọc
            meta = {}
        out.append({"id": aid, "name": str(meta.get("name") or aid), "role": str(meta.get("role") or ""),
                    "phase": phase_of(aid)})
    return out


def _assigned(board) -> dict:
    """task_id → agent theo ASSIGN cuối cùng trong trace_log."""
    out = {}
    for e in board.get("trace_log") or []:
        if str(e.get("event") or "").upper() == EventType.ASSIGN and e.get("task_id") and e.get("agent"):
            out[e["task_id"]] = e["agent"]
    return out


def _owner(task: dict, assigned: dict):
    st = task.get("status") or "ready"
    if st in SUBSTATE_AGENT:
        return SUBSTATE_AGENT[st]
    base = assigned.get(task.get("id")) or task.get("agent")
    if st in ("in_dev", BLOCKED):
        return base or DEFAULT_AGENT
    return base


def tasks(board) -> list[dict]:
    assigned = _assigned(board)
    out = []
    for t in board.get("task_backlog") or []:
        title = t.get("title") or t.get("spec") or t.get("story_ref") or ""
        out.append({"id": t.get("id"), "title": team_feed._cut(title, TITLE_MAX),
                    "status": t.get("status") or "ready", "owner_agent": _owner(t, assigned),
                    "deps": list(t.get("deps") or []), "owns": list(t.get("owns") or []),
                    "risk_level": t.get("risk_level")})
    return out


def _with_route(ev: dict, frm, to, *, parent=None, agent=None) -> dict:
    return {"ts": ev["ts"], "type": ev["type"], "from": frm, "to": to, "summary": ev["summary"],
            "task_id": ev["task_id"], "parent": parent, "agent": agent, "source": ev["source"]}


def events(board) -> list[dict]:
    """Event Team feed + from/to. `id` = <zone>:<chỉ số> (zone append-only → ổn định giữa các lần poll)."""
    out, assigned = [], {}
    # Tái dùng hàm dựng summary của team_feed theo TỪNG entry để giữ đúng whitelist field.
    for i, e in enumerate(board.get("trace_log") or []):
        base = team_feed._trace_events([e])
        if not base:
            continue
        ev, kind, tid = base[0], base[0]["type"], e.get("task_id")
        by = e.get("by") or e.get("source_agent") or PARENT
        if kind == EventType.ASSIGN:
            if tid and e.get("agent"):
                assigned[tid] = e["agent"]
            r = _with_route(ev, by, e.get("agent"), agent=e.get("agent"))
        elif kind == EventType.SPAWN:
            agent = assigned.get(tid) or DEFAULT_AGENT
            r = _with_route(ev, e.get("parent") or by, agent, parent=e.get("parent") or by, agent=agent)
        elif kind == EventType.RESULT:
            agent = assigned.get(tid) or (None if tid else e.get("agent"))
            r = _with_route(ev, agent or by, by if agent else None, agent=agent)
        elif kind == EventType.KILL:
            r = _with_route(ev, by, assigned.get(tid), agent=assigned.get(tid))
        else:                                                   # BLOCKED
            agent = assigned.get(tid) or (None if tid else e.get("agent"))
            r = _with_route(ev, agent or by, None, agent=agent)
        out.append({"id": f"trace_log:{i}", **r})
    for i, e in enumerate(board.get("huddle_log") or []):
        ev = team_feed._huddle_events([e])[0]
        out.append({"id": f"huddle_log:{i}", **_with_route(ev, e.get("agent"), HUDDLE, agent=e.get("agent"))})
    for i, r in enumerate(board.get("decision_records") or []):
        ev = team_feed._decision_events([r])[0]
        out.append({"id": f"decision_records:{i}", **_with_route(ev, HUDDLE, None)})
    rr = board.get("risk_register") or []
    done = team_feed.resolved_indexes(rr)
    for i, r in enumerate(rr):
        evs = team_feed._risk_events([r], done, offset=i)
        if evs:
            out.append({"id": f"risk_register:{i}", **_with_route(evs[0], r.get("source_agent"), None,
                                                                  agent=r.get("source_agent"))})
    order = {e["id"]: n for n, e in enumerate(out)}
    out.sort(key=lambda e: (e["ts"] is None, e["ts"] or 0, order[e["id"]]))
    return out


ESCALATION_BY = "orchestrator.escalation"   # src/ops/escalation.py: người gỡ → xoá 'blocked' của bước phase
ESCALATION_RESOLVE = ("retry", "backtrack", "abandon")


def step_status(board) -> dict:
    """agent → working | blocked theo bước phase (trace_log, task_id rỗng — src/ops/pipeline.assign/finish):
    ASSIGN chưa có RESULT/BLOCKED → working; BLOCKED cuối → blocked (tới ASSIGN kế tiếp hoặc người gỡ
    ESCALATED); RESULT → hết (idle)."""
    st: dict = {}
    for e in board.get("trace_log") or []:
        kind = str(e.get("event") or "").upper()
        if kind == EventType.DECISION and e.get("by") == ESCALATION_BY and e.get("decision") in ESCALATION_RESOLVE:
            st = {a: s for a, s in st.items() if s != "blocked"}
            continue
        if e.get("task_id") or not e.get("agent"):
            continue
        if kind == EventType.ASSIGN:
            st[e["agent"]] = "working"
        elif kind == EventType.RESULT:
            st.pop(e["agent"], None)
        elif kind == EventType.BLOCKED:
            st[e["agent"]] = "blocked"
    return st


def _risk_blocked_agents(board) -> set:
    return {r.get("source_agent") for r in team_feed.open_risks(board.get("risk_register"))
            if str(r.get("severity") or "").lower() in team_feed.BLOCKING_SEVERITY}


def agent_meta(root=None) -> list[dict]:
    """Danh sách agent (id, name, role, phase) từ specs — caller gọi nhiều snapshot có thể dùng lại."""
    return _agent_meta(root)


def snapshot(board, *, active=(), gate: dict | None = None, levels: dict | None = None,
             limit: int | None = EVENT_LIMIT, root=None, meta: list[dict] | None = None) -> dict:
    """Payload GET /api/room. `active`: agent đang chạy bước hiện tại (caller biết, vd dashboard demo);
    `gate`: {id, agents} khi đang chờ người ký; `levels`: agent → 'L0'..'L2' (Autonomy, nếu có);
    `meta`: kết quả agent_meta() dựng sẵn (không thì đọc specs)."""
    ts = tasks(board)
    evs = events(board)
    if limit:
        evs = evs[-int(limit):]
    gate_agents = set((gate or {}).get("agents") or [])
    risk_blocked = _risk_blocked_agents(board)
    steps = step_status(board)
    agents = []
    for a in (meta if meta is not None else _agent_meta(root)):
        mine = [t for t in ts if t["owner_agent"] == a["id"] and t["status"] in (*WORKER_STATES, BLOCKED)]
        step = steps.get(a["id"])
        if any(t["status"] == BLOCKED for t in mine) or a["id"] in risk_blocked or step == "blocked":
            status = "blocked"
        elif a["id"] in gate_agents:
            status = "waiting_gate"
        elif mine or a["id"] in active or step == "working":
            status = "working"
        else:
            status = "idle"
        cur = next((t["id"] for t in mine if t["status"] != BLOCKED), None) or (mine[0]["id"] if mine else None)
        agents.append({**a, "status": status, "current_task": cur, "tasks": [t["id"] for t in mine],
                       "level": (levels or {}).get(a["id"])})
    return {"phases": PHASES, "statuses": list(STATUSES), "agents": agents, "tasks": ts, "events": evs,
            "gate": ({"id": gate.get("id"), "agents": sorted(gate_agents)} if gate else None)}
