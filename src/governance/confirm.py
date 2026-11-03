"""Xác nhận của NGƯỜI — một chỗ, dùng chung cho Blackboard.sign, factory/interview.confirm,
e2_cos.confirm/submit và autonomy.promote.

Danh sách "không phải người" (MACHINE_IDS), dựng từ nguồn sự thật, không hard-code tên agent:
  writer của mọi zone (specs/05_blackboard.yaml) ∪ agent có spec (specs/02_agents/*.md)
  ∪ hệ thống {orchestrator, gateway, dispatcher} ∪ promote.reject_approvers (config/autonomy.yaml)
  − GATE_UI (gate_ui là kênh ký gate, xử lý riêng ở Blackboard.sign).
Chuẩn hoá danh tính trước khi so (_tokens): NFKC + casefold + strip, tách theo `:` `-` `_` `.` và khoảng
trắng. Máy nếu: chuỗi token của một id máy (vd c1_dev → c1,dev) xuất hiện liên tiếp trong danh tính, hoặc
một phần bắt đầu bằng 'auto'. Vd "Orchestrator", "c1-dev", "huddle:a1_bu", "AUTO-L3",
"orchestrator.dispatcher" đều là máy.
Với xác nhận/duyệt (is_human), GATE_UI (mọi biến thể: Gate-UI, gate.ui...) cũng KHÔNG phải người.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
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


_SEP = re.compile(r"[\s:\-_.]+")


def _tokens(who: object) -> tuple[str, ...]:
    """Danh tính chuẩn hoá thành các phần: NFKC, casefold, tách theo : - _ . và khoảng trắng."""
    w = unicodedata.normalize("NFKC", str(who or "")).casefold().strip()
    return tuple(p for p in _SEP.split(w) if p)


def _contains(hay: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


_MACHINE_TOKENS: frozenset[tuple[str, ...]] = frozenset(t for t in map(_tokens, MACHINE_IDS) if t)
_GATE_UI_TOKENS = _tokens(GATE_UI)


def is_machine(who: object, extra: Iterable[str] = ()) -> bool:
    """True nếu `who` (sau chuẩn hoá) chứa danh tính máy (agent/hệ thống/auto-*) hoặc thuộc `extra`."""
    parts = _tokens(who)
    if not parts:
        return False
    deny = _MACHINE_TOKENS | {t for t in map(_tokens, extra) if t}
    return any(p.startswith(AUTO_PREFIX) for p in parts) or any(_contains(parts, d) for d in deny)


def is_human(who: object, extra: Iterable[str] = ()) -> bool:
    """Người thật: không rỗng, không phải máy, không phải kênh gate_ui (mọi biến thể)."""
    parts = _tokens(who)
    return bool(parts) and parts != _GATE_UI_TOKENS and not is_machine(who, extra)


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
