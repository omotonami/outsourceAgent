"""Quy tắc escalate sang người."""
from pathlib import Path
import yaml

MAX_LOOPS = yaml.safe_load((Path(__file__).parent / "transitions.yaml").read_text(encoding="utf-8"))["max_build_loops"]


def should_escalate(loops: int, tier: str, failed: bool, over_budget: bool) -> str | None:
    if over_budget:
        return "vượt budget"
    if loops > MAX_LOOPS:
        return f"build loop > {MAX_LOOPS} vòng"
    if tier == "opus" and failed:
        return "Opus vẫn fail"
    return None
