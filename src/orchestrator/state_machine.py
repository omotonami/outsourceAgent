"""State machine của Orchestrator. Không bước nào do LLM quyết định."""
from pathlib import Path
import yaml
from . import gates

_CFG = yaml.safe_load((Path(__file__).parent / "transitions.yaml").read_text(encoding="utf-8"))
ORDER = _CFG["states"]


class TransitionRefused(Exception):
    pass


class Orchestrator:
    def __init__(self, board, state: str = "INTAKE"):
        self.board = board
        self.state = state

    def advance(self) -> str:
        for t in _CFG["transitions"]:
            if t["from"] == self.state:
                ok, reason = getattr(gates, t["guard"])(self.board)
                if not ok:
                    raise TransitionRefused(f"{t['guard']}: {reason}")
                self.state = t["to"]
                return self.state
        raise TransitionRefused(f"Không có transition từ {self.state}")

    def backtrack(self, to_state: str, reason: str, by: str) -> None:
        """Quay lại state trước; huỷ mọi chữ ký gate phía sau state đó."""
        if ORDER.index(to_state) >= ORDER.index(self.state):
            raise TransitionRefused("Backtrack chỉ đi lùi")
        for st, gate in _CFG["gate_states"].items():
            if ORDER.index(st) > ORDER.index(to_state):
                self.board.revoke_signature(gate, reason=reason, by=by)
        self.state = to_state

    def escalate(self, reason: str) -> None:
        self.board.append("risk_register", {"risk": reason, "source_agent": "orchestrator",
                                            "severity": "High", "status": "open"}, agent="orchestrator")
        self.state = "ESCALATED"
