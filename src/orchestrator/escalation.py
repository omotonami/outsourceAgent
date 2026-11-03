"""Quy tắc escalate sang người."""
from src.common import config

MAX_LOOPS = config.require(config.load_yaml(config.TRANSITIONS), "max_build_loops", config.TRANSITIONS)


def should_escalate(loops: int, tier: str, failed: bool, over_budget: bool) -> str | None:
    if over_budget:
        return "vượt budget"
    if loops > MAX_LOOPS:
        return f"build loop > {MAX_LOOPS} vòng"
    if tier == "opus" and failed:
        return "Opus vẫn fail"
    return None
