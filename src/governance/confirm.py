"""Xác nhận của NGƯỜI — một chỗ, dùng chung cho Blackboard.sign, factory/interview.confirm,
e2_cos.confirm/submit và autonomy.promote.

Danh sách "không phải người" (MACHINE_IDS), dựng từ nguồn sự thật, không hard-code tên agent:
  writer của mọi zone (specs/05_blackboard.yaml) ∪ agent có spec (specs/02_agents/*.md)
  ∪ hệ thống {orchestrator, gateway, dispatcher} ∪ promote.reject_approvers (config/autonomy.yaml)
  − GATE_UI (gate_ui là kênh ký gate, xử lý riêng ở Blackboard.sign).
Thêm: tên bắt đầu bằng 'auto' (auto-L2, auto-L3...) và '<máy>.<phụ>' (orchestrator.dispatcher) là máy.
Với xác nhận/duyệt (is_human), GATE_UI cũng KHÔNG phải người.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from src.common import config

GATE_UI = "gate_ui"
SYSTEM_IDS = frozenset({"orchestrator", "gateway", "dispatcher"})
AUTO_PREFIX = "auto"


def _zone_writers() -> set[str]:
    zones = config.require(config.load_yaml("specs/05_blackboard.yaml"), "zones", "specs/05_blackboard.yaml")
    return {w for z in zones.values() for w in z["writers"]}


def _reject_approvers() -> set[str]:
    cfg = config.load_yaml(config.AUTONOMY)
    return set((cfg.get("promote") or {}).get("reject_approvers") or [])


MACHINE_IDS: frozenset[str] = frozenset(
    (_zone_writers() | config.agent_ids() | SYSTEM_IDS | _reject_approvers()) - {GATE_UI})


def is_machine(who: object, extra: Iterable[str] = ()) -> bool:
    """True nếu `who` là danh tính máy (agent/hệ thống/auto-*) hoặc nằm trong `extra`."""
    w = str(who or "").strip()
    deny = MACHINE_IDS | set(extra)
    return w in deny or w.split(".", 1)[0] in deny or w.lower().startswith(AUTO_PREFIX)


def is_human(who: object, extra: Iterable[str] = ()) -> bool:
    """Người thật: không rỗng, không phải máy, không phải kênh gate_ui."""
    w = str(who or "").strip()
    return bool(w) and w != GATE_UI and not is_machine(w, extra)


def require_human(who: object, error: type[Exception], message: str, extra: Iterable[str] = ()) -> str:
    """Trả tên người (đã strip) hoặc raise `error(message.format(who=<chuỗi gốc>))`."""
    raw = str(who or "")
    if not is_human(raw, extra):
        raise error(message.format(who=raw))
    return raw.strip()


def payload_hash(payload: object) -> str:
    """Hash ổn định (sort_keys) của nội dung đã xác nhận — sửa sau khi xác nhận → hash lệch."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
