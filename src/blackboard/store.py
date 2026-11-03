"""Blackboard: shared state, ghi theo vùng có kiểm tra quyền + schema."""
import copy
from pathlib import Path

from jsonschema import validate

from src.common import config
from src.governance import confirm

_HERE = Path(__file__).parent
# Một nguồn duy nhất: specs/05_blackboard.yaml (không còn bản sao src/blackboard/permissions.yaml — 2026-11-03).
ZONES_FILE = config.path("specs/05_blackboard.yaml")
_PERM = config.require(config.load_yaml(ZONES_FILE), "zones", "specs/05_blackboard.yaml")
GATE_UI = confirm.GATE_UI


class PermissionDenied(Exception):
    pass


def _schema(zone):
    """Schema zone (src/blackboard/schema/<zone>.json) hoặc None nếu zone không có schema."""
    p = _HERE / "schema" / f"{zone}.json"
    return config.load_json(p) if p.exists() else None


class Blackboard:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self._data: dict = {}
        self._history: list = []
        self._signatures: dict = {}

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
        self._history.append((zone, agent, copy.deepcopy(self._data.get(zone))))
        self._data[zone] = value

    def append(self, zone, entry, agent):
        self._check(zone, agent, "append_only")
        self._data.setdefault(zone, []).append({**entry, "source_agent": entry.get("source_agent", agent)})

    # Chữ ký gate: chỉ gate_ui (hoặc người ký qua gate_ui). Agent/hệ thống/auto-* bị từ chối
    # (danh sách máy dùng chung: src/governance/confirm.py), kể cả khi khai via=gate_ui.
    def sign(self, gate, by, via=None):
        who = str(by or "").strip()
        machine = confirm.is_machine(who)
        if not who or machine or not (who == GATE_UI or via == GATE_UI):
            raise PermissionDenied(f"chỉ {GATE_UI} được ký gate (by={by!r}, via={via!r})")
        self._signatures[gate] = {"by": who, "via": GATE_UI, "valid": True}

    def revoke_signature(self, gate, reason, by):
        if gate in self._signatures:
            self._signatures[gate] = {**self._signatures[gate], "valid": False, "revoked_reason": reason, "revoked_by": by}

    def is_signed(self, gate):
        return self._signatures.get(gate, {}).get("valid", False)
