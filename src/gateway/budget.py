"""Theo dõi token theo dự án + phase."""
from collections import defaultdict
from pathlib import Path
import yaml

_CFG = yaml.safe_load((Path(__file__).parents[2] / "config/budgets.yaml").read_text(encoding="utf-8"))
_USED: dict[tuple[str, str], int] = defaultdict(int)


class BudgetExceeded(Exception):
    pass


def check(project_id: str, phase: str) -> None:
    cap = _CFG["per_project_tokens"].get(phase)
    if cap and _USED[(project_id, phase)] >= cap * _CFG["block_auto_escalate_at_ratio"]:
        raise BudgetExceeded(f"{project_id}/{phase} vượt budget, cần người duyệt")


def add(project_id: str, phase: str, tokens: int) -> None:
    _USED[(project_id, phase)] += tokens
