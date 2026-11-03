"""Blackboard: shared state, ghi theo vùng có kiểm tra quyền + schema."""
import copy, json
from pathlib import Path
import yaml
from jsonschema import validate

_HERE = Path(__file__).parent
_PERM = yaml.safe_load((_HERE / "permissions.yaml").read_text(encoding="utf-8"))["zones"]
GATE_UI = "gate_ui"
# Danh tính máy (agent/hệ thống) không bao giờ được ký gate, kể cả khi khai via=gate_ui.
_AGENT_IDS = {p.stem for p in (_HERE.parents[1] / "specs/02_agents").glob("*.md")}
_MACHINE_IDS = ({w for z in _PERM.values() for w in z["writers"]} | _AGENT_IDS
                | {"orchestrator", "gateway", "dispatcher"}) - {GATE_UI}


class PermissionDenied(Exception):
    pass


def _schema(zone):
    p = _HERE / "schema" / f"{zone}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


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

    # Chữ ký gate: chỉ gate_ui (hoặc người ký qua gate_ui). Agent/hệ thống/auto-* bị từ chối.
    def sign(self, gate, by, via=None):
        who = str(by or "").strip()
        machine = (who in _MACHINE_IDS or who.split(".", 1)[0] in _MACHINE_IDS
                   or who.lower().startswith("auto"))
        if not who or machine or not (who == GATE_UI or via == GATE_UI):
            raise PermissionDenied(f"chỉ {GATE_UI} được ký gate (by={by!r}, via={via!r})")
        self._signatures[gate] = {"by": who, "via": GATE_UI, "valid": True}

    def revoke_signature(self, gate, reason, by):
        if gate in self._signatures:
            self._signatures[gate] = {**self._signatures[gate], "valid": False, "revoked_reason": reason, "revoked_by": by}

    def is_signed(self, gate):
        return self._signatures.get(gate, {}).get("valid", False)
