"""AC phải testable: dạng Given/When/Then."""
import re

_GWT = re.compile(r"\bgiven\b.*\bwhen\b.*\bthen\b", re.IGNORECASE | re.DOTALL)


def ac_ok(ac: str) -> bool:
    return bool(_GWT.search(ac or ""))


def story_ok(story: dict) -> bool:
    acs = story.get("AC") or []
    return bool(acs) and all(ac_ok(a) for a in acs)


def ac_pass_rate(stories: list[dict]) -> float:
    if not stories:
        return 0.0
    return sum(story_ok(s) for s in stories) / len(stories)
