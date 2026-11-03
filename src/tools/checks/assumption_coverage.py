"""Mọi assumption + exclusion của estimate phải xuất hiện trong proposal."""


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def coverage(estimate: dict, proposal: dict) -> dict:
    required = (estimate.get("assumptions") or []) + (estimate.get("exclusions") or [])
    text = _norm(" ".join([proposal.get("assumptions_section", ""), proposal.get("exclusions_section", "")]))
    missing = [r for r in required if _norm(r) not in text]
    ratio = 1.0 if not required else (len(required) - len(missing)) / len(required)
    return {"ratio": ratio, "missing": missing}
