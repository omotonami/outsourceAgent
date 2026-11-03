"""Blackboard: shared state, ghi theo vùng có kiểm tra quyền + schema.

Ghi xuyên (write-through) vào event store dùng chung (src/blackboard/eventstore.py) khi được cấu hình:
`Blackboard(pid, store=EventStore(...))` hoặc config/runtime.yaml eventstore.write_through=true.
Store chỉ nhận lượt ghi SAU khi đã kiểm quyền + schema (ghi bị từ chối không vào store); store lỗi →
lượt ghi lỗi, board trong RAM không đổi. Không cấu hình → hành vi cũ (chỉ RAM).
`Blackboard.load(pid, store)` dựng lại trạng thái từ log — bản xem cho server (không ghi xuyên).
"""
import copy
import threading
from pathlib import Path

from jsonschema import validate

from src.common import config
from src.governance import confirm

_HERE = Path(__file__).parent
# Một nguồn duy nhất: specs/05_blackboard.yaml (không còn bản sao src/blackboard/permissions.yaml — 2026-11-03).
ZONES_FILE = config.path("specs/05_blackboard.yaml")
_PERM = config.require(config.load_yaml(ZONES_FILE), "zones", "specs/05_blackboard.yaml")
GATE_UI = confirm.GATE_UI
USAGE_ZONE = "llm_usage"      # = src/orchestrator/usage.ZONE (dòng token trong event store)


class PermissionDenied(Exception):
    pass


def _schema(zone):
    """Schema zone (src/blackboard/schema/<zone>.json) hoặc None nếu zone không có schema."""
    p = _HERE / "schema" / f"{zone}.json"
    return config.load_json(p) if p.exists() else None


_UNSET = object()


class Blackboard:
    def __init__(self, project_id: str, store=_UNSET):
        self.project_id = project_id
        self._data: dict = {}
        self._history: list = []
        self._signatures: dict = {}
        if store is _UNSET:
            from src.blackboard import eventstore
            store = eventstore.configured_store()
        self._store = store
        if store is not None:
            from src.blackboard import eventstore
            eventstore.register(project_id, store)
        self._wlock = threading.RLock()     # thứ tự seq trong store khớp thứ tự áp vào RAM

    def _log(self, kind, payload, zone=None, agent=None):
        if self._store is not None:
            self._store.append(self.project_id, kind, payload, zone=zone, agent=agent)

    @classmethod
    def load(cls, project_id: str, store, upto: int | None = None) -> "Blackboard":
        """Dựng lại board từ log (đến seq `upto` nếu có). Board trả về KHÔNG ghi xuyên (store=None)."""
        b = cls(project_id, store=None)
        for row in store.read(project_id, upto=upto):
            b._replay(row)
        return b

    def _replay(self, row) -> None:
        """Áp MỘT dòng log (đã qua kiểm quyền lúc ghi) vào board dựng lại. Board có store → từ chối."""
        if self._store is not None:
            raise PermissionDenied("chỉ board dựng lại từ log (không ghi xuyên) mới được replay")
        if row.zone == USAGE_ZONE:          # số liệu token (trace gateway), không phải zone Blackboard
            return
        p = copy.deepcopy(row.payload)
        if row.kind == "zone_write":
            self._history.append((row.zone, row.agent, self._data.get(row.zone)))
            self._data[row.zone] = p
        elif row.kind in ("zone_append", "event"):
            self._data.setdefault(row.zone, []).append(p)
        elif row.kind == "sign":
            self._signatures[p["gate"]] = {"by": p.get("by"), "via": p.get("via"), "valid": True}
        elif row.kind == "revoke" and p.get("gate") in self._signatures:
            self._signatures[p["gate"]] = {**self._signatures[p["gate"]], "valid": False,
                                           "revoked_reason": p.get("reason"), "revoked_by": p.get("by")}

    def get(self, zone):
        return copy.deepcopy(self._data.get(zone))

    def _check(self, zone, agent, mode):
        rule = _PERM[zone]
        if rule["mode"] != mode:
            raise PermissionDenied(f"{zone} chỉ cho {rule['mode']}")
        if "*" not in rule["writers"] and agent not in rule["writers"]:
            raise PermissionDenied(f"{agent} không được ghi {zone}")

    def write(self, zone, value, agent):
        self._check(zone, agent, "owner_write")
        if (s := _schema(zone)):
            validate(value, s)
        with self._wlock:
            self._log("zone_write", value, zone=zone, agent=agent)
            self._history.append((zone, agent, copy.deepcopy(self._data.get(zone))))
            self._data[zone] = value

    def append(self, zone, entry, agent):
        self._check(zone, agent, "append_only")
        full = {**entry, "source_agent": entry.get("source_agent", agent)}
        with self._wlock:
            # trace_log = sự kiện điều phối (events.emit) → kind "event"; zone append-only khác → zone_append
            self._log("event" if zone == "trace_log" else "zone_append", full, zone=zone, agent=agent)
            self._data.setdefault(zone, []).append(full)

    # Chữ ký gate: chỉ gate_ui (hoặc người ký qua gate_ui). Agent/hệ thống/auto-* bị từ chối
    # (danh sách máy dùng chung: src/governance/confirm.py), kể cả khi khai via=gate_ui.
    def sign(self, gate, by, via=None):
        who = str(by or "").strip()
        machine = confirm.is_machine(who)
        if not who or machine or not (who == GATE_UI or via == GATE_UI):
            raise PermissionDenied(f"chỉ {GATE_UI} được ký gate (by={by!r}, via={via!r})")
        with self._wlock:
            self._log("sign", {"gate": gate, "by": who, "via": GATE_UI})
            self._signatures[gate] = {"by": who, "via": GATE_UI, "valid": True}

    def revoke_signature(self, gate, reason, by):
        if gate in self._signatures:
            with self._wlock:
                self._log("revoke", {"gate": gate, "reason": reason, "by": by})
                self._signatures[gate] = {**self._signatures[gate], "valid": False, "revoked_reason": reason,
                                          "revoked_by": by}

    def is_signed(self, gate):
        return self._signatures.get(gate, {}).get("valid", False)
