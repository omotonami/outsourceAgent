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

Review PR #2: bỏ ký tự Unicode category Cf (zero-width, BOM, soft hyphen…) TRƯỚC NFKC; tách thêm theo `@` `/`;
danh sách máy thêm BUILTIN_MACHINE_IDS (claude, claude-code, assistant, bot, chief, chief-ops) +
config/governance.yaml confirm.machine_ids_extra; dạng VIẾT LIỀN của mọi id máy (c1_dev → "c1dev", gate_ui →
"gateui", "orch estrator" → "orchestrator") cũng là máy — sinh tự động, không liệt kê tay.

LƯU Ý: đây là lớp CHỐNG DÙNG NHẦM (bot/agent tự điền danh tính máy hoặc tên mình), KHÔNG PHẢI XÁC THỰC — ai gõ
một tên người bất kỳ vẫn qua. Bảo đảm "người thật ký" nằm ở kênh: gate_ui (dashboard) / terminal TTY do người gõ
(CLAUDE.md luật 10) và .claude/settings.json chặn Claude chạy các đường đó.
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
BUILTIN_MACHINE_IDS = frozenset({"claude", "claude-code", "assistant", "bot", "chief", "chief-ops"})
AUTO_PREFIX = "auto"
GOVERNANCE = "config/governance.yaml"


def _zone_writers() -> set[str]:
    zones = config.require(config.load_yaml("specs/05_blackboard.yaml"), "zones", "specs/05_blackboard.yaml")
    return {w for z in zones.values() for w in z["writers"]}


def _reject_approvers() -> set[str]:
    cfg = config.load_yaml(config.AUTONOMY)
    return set((cfg.get("promote") or {}).get("reject_approvers") or [])


def _governance_extra() -> set[str]:
    """confirm.machine_ids_extra trong config/governance.yaml (file không có → rỗng)."""
    if not config.path(GOVERNANCE).exists():
        return set()
    cfg = config.load_yaml(GOVERNANCE) or {}
    extra = (cfg.get("confirm") or {}).get("machine_ids_extra") or []
    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        raise config.ConfigError(f"{GOVERNANCE}: confirm.machine_ids_extra phải là danh sách chuỗi")
    return set(extra)


MACHINE_IDS: frozenset[str] = frozenset(
    (_zone_writers() | config.agent_ids() | SYSTEM_IDS | _reject_approvers() | BUILTIN_MACHINE_IDS
     | _governance_extra()) - {GATE_UI})


_SEP = re.compile(r"[\s:\-_.@/]+")


def _strip_cf(s: str) -> str:
    """Bỏ ký tự Unicode category Cf (format: zero-width space/joiner, BOM, soft hyphen, bidi…)."""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def _tokens(who: object) -> tuple[str, ...]:
    """Danh tính chuẩn hoá thành các phần: bỏ Cf, NFKC, casefold, tách theo : - _ . @ / và khoảng trắng."""
    w = unicodedata.normalize("NFKC", _strip_cf(str(who or ""))).casefold()
    w = _strip_cf(w).strip()                 # NFKC có thể sinh lại Cf hiếm gặp → bỏ lần nữa
    return tuple(p for p in _SEP.split(w) if p)


def _contains(hay: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


_MACHINE_TOKENS: frozenset[tuple[str, ...]] = frozenset(t for t in map(_tokens, MACHINE_IDS) if t)
_MACHINE_JOINED: frozenset[str] = frozenset("".join(t) for t in _MACHINE_TOKENS)
_GATE_UI_TOKENS = _tokens(GATE_UI)
_GATE_UI_JOINED = "".join(_GATE_UI_TOKENS)


def _joined_windows(parts: tuple[str, ...]):
    """Mọi dãy phần liên tiếp viết liền: ("orch","estrator") → "orch", "orchestrator", "estrator"."""
    for i in range(len(parts)):
        for j in range(i + 1, len(parts) + 1):
            yield "".join(parts[i:j])


def is_machine(who: object, extra: Iterable[str] = ()) -> bool:
    """True nếu `who` (sau chuẩn hoá) chứa danh tính máy (agent/hệ thống/auto-*) hoặc thuộc `extra` — kể cả
    dạng viết liền / tách rời. Lớp chống dùng nhầm, KHÔNG phải xác thực (xem docstring module)."""
    parts = _tokens(who)
    if not parts:
        return False
    ext = {t for t in map(_tokens, extra) if t}
    deny = _MACHINE_TOKENS | ext
    joined = _MACHINE_JOINED | {"".join(t) for t in ext}
    return (any(p.startswith(AUTO_PREFIX) for p in parts) or any(_contains(parts, d) for d in deny)
            or any(w in joined for w in _joined_windows(parts)))


def is_human(who: object, extra: Iterable[str] = ()) -> bool:
    """Người thật (theo nghĩa CHỐNG DÙNG NHẦM, không phải xác thực): không rỗng, không phải máy, không phải
    kênh gate_ui (mọi biến thể, kể cả viết liền "gateui")."""
    parts = _tokens(who)
    return (bool(parts) and parts != _GATE_UI_TOKENS and _GATE_UI_JOINED not in _joined_windows(parts)
            and not is_machine(who, extra))


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
