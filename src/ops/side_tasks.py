"""Hàng đợi task giao thêm cho role (việc phụ / điều tra) — zone `side_tasks` (append-only, writer
orchestrator; specs/05_blackboard.yaml). Code thuần.

Mỗi task là chuỗi bản ghi append-only cùng `id`:
  {event: queued,  id, role, title, detail, kind: main|side, via: cli|dashboard[, confirmed_by: <người gõ>]}
  {event: started, id, role}
  {event: done,    id, role, note: {summary, findings[], risks[], recommendations[]}, tier}
  {event: failed,  id, role, reason}
Trạng thái = bản ghi cuối của id. Người giao qua CLI (`python -m src.ops.project task add`) hoặc Control Room
(gõ tên + XAC NHAN). Chạy: scheduler (src/ops/scheduler.py) gọi run_one() — agent chạy CHỈ ĐỌC
(src/agents/side_task.py), kết quả là note trong zone này; KHÔNG ghi zone sản phẩm, không ký, không gỡ.
Phòng 3D: ASSIGN/RESULT trong trace_log có kind=side + side_task=<id> (RESULT ok=False khi lỗi — không BLOCKED,
việc phụ hỏng không chặn role).
"""
from __future__ import annotations

import secrets
import time

from src.agents import side_task
from src.common import config
from src.orchestrator import events, room_state

ZONE = room_state.SIDE_ZONE
WRITER = "orchestrator"
KINDS = ("main", "side")
VIAS = ("cli", "dashboard")
TITLE_MAX = 160
DETAIL_MAX = 4000
OPEN = ("queued", "started")


class TaskError(ValueError):
    pass


def _clean(s, n) -> str:
    s = " ".join(str(s or "").split()) if n <= TITLE_MAX else str(s or "").strip()
    return s[:n]


def add(board, role: str, title: str, *, detail: str = "", kind: str = "side", via: str = "cli",
        confirmed_by: str | None = None) -> dict:
    """Xếp hàng một task. confirmed_by chỉ do người gõ (dashboard) — caller kiểm require_human."""
    if role not in config.agent_ids():
        raise TaskError(f"role {role!r} không có spec trong specs/02_agents")
    if kind not in KINDS:
        raise TaskError(f"kind {kind!r} — chỉ {KINDS}")
    if via not in VIAS:
        raise TaskError(f"via {via!r} — chỉ {VIAS}")
    t = _clean(title, TITLE_MAX)
    if not t:
        raise TaskError("thiếu title")
    entry = {"event": "queued", "id": "S-" + secrets.token_hex(3), "role": role, "title": t,
             "detail": _clean(detail, DETAIL_MAX), "kind": kind, "via": via, "ts": time.time()}
    if confirmed_by:
        entry["confirmed_by"] = confirmed_by
    board.append(ZONE, entry, agent=WRITER)
    return entry


def tasks(board) -> list[dict]:
    """[{id, role, title, detail, kind, via, status, note?, reason?}] theo thứ tự xếp hàng (room_state.side_tasks)."""
    return room_state.side_tasks(board)


def pending(board) -> list[dict]:
    return [t for t in tasks(board) if t["status"] == "queued"]


def run_one(board, task: dict, pid: str, sender=None) -> dict:
    """Chạy MỘT task: started → agent chỉ đọc → done (note) | failed. Trả bản ghi cuối."""
    role, sid = task["role"], task["id"]
    board.append(ZONE, {"event": "started", "id": sid, "role": role, "ts": time.time()}, agent=WRITER)
    label = f"việc {'phụ' if task.get('kind') == 'side' else 'chính'} {sid}: {task.get('title') or ''}"[:120]
    events.emit(board, "ASSIGN", writer=WRITER, by=WRITER, agent=role, phase=label, project=pid,
                kind=task.get("kind") or "side", side_task=sid, **{"from": WRITER, "to": role})
    try:
        res = side_task.run(board, role, task, pid, sender=sender)
    except Exception as e:  # noqa: BLE001 — lỗi môi trường (key, mạng, budget): ghi failed, không sập scheduler
        res = {"error": type(e).__name__, "reason": str(e)[:160]}
    if res.get("ok"):
        end = {"event": "done", "id": sid, "role": role, "note": res["data"], "tier": res.get("tier"), "ts": time.time()}
    else:
        end = {"event": "failed", "id": sid, "role": role, "ts": time.time(),
               "reason": f"{res.get('error')}: {res.get('reason') or ''}"[:200]}
    board.append(ZONE, end, agent=WRITER)
    events.emit(board, "RESULT", writer=WRITER, by=WRITER, agent=role, phase=label, project=pid,
                kind=task.get("kind") or "side", side_task=sid, ok=bool(res.get("ok")),
                **({} if res.get("ok") else {"reason": end["reason"]}), **{"from": role, "to": WRITER})
    return end
