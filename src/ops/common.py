"""Dùng chung cho src/ops: mở event store, dựng board, đọc state, số liệu token. Code thuần."""
from __future__ import annotations

import time
from pathlib import Path

from src.blackboard import eventstore
from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.common import config
from src.gateway import budget as ledger
from src.orchestrator import gate_view, usage
from src.orchestrator.state_machine import ORDER

GATE_STATES = gate_view.STATE_GATE
GATE_SIGNER = gate_view.GATE_SIGNER
TERMINAL = ("RELEASED", "ESCALATED", "ABANDONED")
DASHBOARD = "uvicorn src.server.app:app  →  http://localhost:8000"


def open_store(db: str | Path | None = None, *, readonly: bool = False) -> EventStore:
    """Store theo --db, không thì OA_EVENTSTORE_PATH / config/runtime.yaml (cùng file dashboard đọc)."""
    return EventStore(Path(db) if db else eventstore.default_path(), readonly=readonly)


def exists(store: EventStore, pid: str) -> bool:
    return store.last_seq(pid) > 0


def state_of(board) -> str:
    """State hiện tại = project_status.state mới nhất (Orchestrator ghi). Chưa có → INTAKE."""
    st = (board.get("project_status") or {}).get("state")
    return st if st in ORDER else "INTAKE"


def pending_gate(state: str) -> str | None:
    return GATE_STATES.get(state)


def usage_rows(store: EventStore, pid: str) -> list[dict]:
    return [r.payload for r in store.read(pid) if r.zone == usage.ZONE]


def usage_of(store: EventStore, pid: str) -> usage.Usage:
    u = usage.Usage()
    for r in usage_rows(store, pid):
        u.add_record(r)
    return u


def seed_ledger(store: EventStore, pid: str) -> dict[str, int]:
    """Sổ budget là RAM theo process → nạp lại token đã dùng (theo phase) từ event store, để trần
    config/budgets.yaml có hiệu lực xuyên nhiều lần chạy / nhiều ngày."""
    ledger.reset(pid)
    by_phase: dict[str, int] = {}
    for r in usage_rows(store, pid):
        ph = r.get("phase") or "?"
        by_phase[ph] = by_phase.get(ph, 0) + int(r.get("total") or 0)
    for ph, n in by_phase.items():
        ledger.add(pid, ph, n)
    return by_phase


def budget_ratio(store: EventStore, pid: str) -> float:
    b = usage.budget()
    if not b or not b["limit"]:
        return 0.0
    return usage_of(store, pid).total / b["limit"]


def resume(store: EventStore, pid: str) -> Blackboard:
    return Blackboard.resume(pid, store)


def new_id(now: float | None = None) -> str:
    """Mã project mặc định KHÔNG chứa tên client (để bản --redact giữ được mã)."""
    return time.strftime("P-%Y%m%d-%H%M%S", time.localtime(now or time.time()))


def rate_card() -> dict:
    return config.load_yaml("data/rate_cards/default.yaml")
