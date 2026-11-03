"""Chọn tier model bằng Code."""
from pathlib import Path
import yaml

TIERS = ["haiku", "sonnet", "opus"]
_CFG = yaml.safe_load((Path(__file__).parent / "routing.yaml").read_text(encoding="utf-8"))


def pick_tier(agent: str, step: str = "default", risk_level: str = "normal",
              signals: set[str] | None = None, generator_tier: str | None = None) -> str:
    rule = _CFG["agents"][agent]
    tier = rule.get(step) or rule.get("default") or "sonnet"
    if tier == "same_or_higher_than_generator":
        tier = generator_tier or "sonnet"
    if risk_level == "high" and "high_risk" in rule:
        tier = rule["high_risk"]
    triggers = set(_CFG["escalate_on"]) & set(signals or [])
    if (triggers or risk_level == "high") and "escalate_to" in rule:
        tier = max(tier, rule["escalate_to"], key=TIERS.index)
    return tier
