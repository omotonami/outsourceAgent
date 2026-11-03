"""Ghi traceability bắt buộc cho mọi output (append-only).

Project có event store (Blackboard ghi xuyên, src/blackboard/eventstore.py) → thêm MỘT dòng số liệu token
(kind=event, zone llm_usage: agent/tier/model/phase/tokens — không prompt, không output) cho Phòng 3D.
Store lỗi không làm hỏng lần gọi model (chỉ cảnh báo).
"""
import logging
import time

from src.common import config, jsonl

LOG = config.ROOT / "eval/runs/trace.jsonl"
_log = logging.getLogger(__name__)


def log(**fields) -> None:
    jsonl.append(LOG, {"ts": time.time(), **fields})
    _to_store(fields)


def _to_store(fields: dict) -> None:
    from src.blackboard import eventstore
    from src.orchestrator import usage
    pid = fields.get("project_id")
    try:
        store = eventstore.store_for(pid)
        if store is not None:
            store.append(pid, "event", usage.record(fields), zone=usage.ZONE, agent=fields.get("agent"))
    except Exception as e:  # noqa: BLE001 — số liệu hiển thị, không chặn luồng model
        _log.warning("không ghi được usage vào event store: %s", e)
