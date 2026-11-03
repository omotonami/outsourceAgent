"""Team feed (H5) — Code thuần, CHỈ ĐỌC Blackboard. Dựng timeline cho người xem "team đang làm gì".

Nguồn → type chuẩn hoá ∈ TYPES:
  trace_log        event SPAWN / RESULT / BLOCKED / KILL / ASSIGN (Dispatcher H2, writer orchestrator)
  huddle_log       lượt nói {round, agent, role, summary}            → RESULT (actor = agent)
  decision_records kết quả huddle                                     → DECISION
  risk_register    risk còn mở, severity High/Critical                 → BLOCKED
Chỉ xuất SUMMARY dựng từ field whitelist (≤ MAX_SUMMARY ký tự) — không bao giờ trả raw thinking,
transcript, prompt, messages, contract hay output đầy đủ. Không ghi Blackboard.
Cây spawn: SPAWN {by|parent → task_id}; trạng thái node = event cuối của task đó.
"""
from src.orchestrator.events import SOURCE_TYPES, TYPES, EventType

MAX_SUMMARY = 200
BLOCKING_SEVERITY = {"high", "critical"}
_CLOSED = {"closed", "resolved"}


def _cut(s, n=MAX_SUMMARY):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _ts(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _trace_events(entries):
    out = []
    for e in entries or []:
        kind = str(e.get("event") or e.get("type") or "").upper()
        if kind not in TYPES:
            continue                         # gateway/khác: không phải sự kiện team
        tid = e.get("task_id")
        if kind == EventType.SPAWN:
            s = f"spawn {tid}" + (f" ({e['trigger']})" if e.get("trigger") else "") \
                + (f" · lần {e['attempt']}" if e.get("attempt") else "")
        elif kind == EventType.RESULT:
            ok = e.get("ok")
            s = f"{tid}: " + ("OK" if ok else f"FAIL — {e.get('reason') or 'không rõ'}") \
                + (" · QC critical" if e.get("qc_critical") else "")
        elif kind == EventType.ASSIGN:
            s = f"giao {tid}" + (f" cho {e['agent']}" if e.get("agent") else "")
        else:                                # BLOCKED / KILL
            s = (f"{tid}: " if tid else "") + str(e.get("reason") or kind.lower())
        out.append({"type": kind, "ts": _ts(e.get("ts")), "actor": e.get("by") or e.get("source_agent"),
                    "task_id": tid, "summary": _cut(s), "source": "trace_log"})
    return out


def _huddle_events(entries):
    return [{"type": SOURCE_TYPES["huddle_log"].value, "ts": _ts(e.get("ts")), "actor": e.get("agent"),
             "task_id": None,
             "summary": _cut(f"huddle vòng {e.get('round', '?')} · {e.get('role', '?')}: {e.get('summary', '')}"),
             "source": "huddle_log"} for e in entries or []]


def _decision_events(entries):
    out = []
    for r in entries or []:
        chosen = r.get("chosen")
        s = f"{r.get('question', '?')} → " + (chosen if chosen else f"chưa chốt ({r.get('stopped_reason') or 'mở'})")
        if r.get("open_questions"):
            s += f" · {len(r['open_questions'])} câu hỏi mở"
        out.append({"type": SOURCE_TYPES["decision_records"].value, "ts": _ts(r.get("ts")), "actor": "huddle", "task_id": None,
                    "summary": _cut(s), "source": "decision_records"})
    return out


def is_open_risk(r: dict) -> bool:
    """Risk còn mở: status rỗng = open; closed/resolved (không phân biệt hoa thường) = đóng."""
    return str(r.get("status") or "open").lower() not in _CLOSED


def _risk_events(entries):
    out = []
    for r in entries or []:
        if not is_open_risk(r):
            continue
        if str(r.get("severity") or "").lower() not in BLOCKING_SEVERITY:
            continue
        out.append({"type": SOURCE_TYPES["risk_register"].value, "ts": _ts(r.get("ts")),
                    "actor": r.get("source_agent"), "task_id": None,
                    "summary": _cut(f"risk {r.get('severity')}: {r.get('risk', '')}"), "source": "risk_register"})
    return out


def events(board):
    """Timeline chuẩn hoá, theo thời gian (event không có ts giữ thứ tự nguồn, xếp sau)."""
    evs = (_trace_events(board.get("trace_log")) + _huddle_events(board.get("huddle_log"))
           + _decision_events(board.get("decision_records")) + _risk_events(board.get("risk_register")))
    for i, e in enumerate(evs):
        e["seq"] = i
    evs.sort(key=lambda e: (e["ts"] is None, e["ts"] or 0, e["seq"]))
    for e in evs:
        del e["seq"]
    return evs


def spawn_tree(board):
    """[{id, status, children:[...]}] — gốc là bên spawn (vd orchestrator.dispatcher)."""
    nodes, parent_of, order = {}, {}, []
    for e in board.get("trace_log") or []:
        kind, tid = str(e.get("event") or "").upper(), e.get("task_id")
        if kind == EventType.SPAWN and tid:
            parent = e.get("parent") or e.get("by") or "orchestrator"
            for n in (parent, tid):
                if n not in nodes:
                    nodes[n] = {"id": n, "status": None, "attempts": 0, "children": []}
                    order.append(n)
            parent_of[tid] = parent
            nodes[tid]["status"] = "SPAWN"
            nodes[tid]["attempts"] += 1
        elif tid in nodes and kind in (EventType.RESULT, EventType.BLOCKED, EventType.KILL):
            nodes[tid]["status"] = ("OK" if e.get("ok") else "FAIL") if kind == EventType.RESULT else kind
    for child, parent in parent_of.items():
        if child != parent and child not in nodes[parent]["children"]:
            nodes[parent]["children"].append(child)

    def build(n, seen):
        node = nodes[n]
        kids = [build(c, seen | {n}) for c in node["children"] if c not in seen]
        return {"id": n, "status": node["status"], "attempts": node["attempts"], "children": kids}

    return [build(n, set()) for n in order if n not in parent_of]


def build(board, limit=None):
    """Payload cho GET /api/feed: {types, events (mới nhất cuối), spawn_tree, counts}."""
    evs = events(board)
    counts = {t: sum(1 for e in evs if e["type"] == t) for t in TYPES}
    if limit:
        evs = evs[-int(limit):]
    return {"types": list(TYPES), "events": evs, "spawn_tree": spawn_tree(board), "counts": counts}
