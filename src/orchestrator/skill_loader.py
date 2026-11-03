"""Nạp skill 3 lớp. _core = specs/02_agents/<role>.md ; team/personal = knowledge/<role>/."""
from pathlib import Path
import re, yaml

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "specs/02_agents"
KN = ROOT / "knowledge"


def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\s*---\n(.*?)\n---\n(.*)", text, re.S)
    meta, body = (yaml.safe_load(m.group(1)), m.group(2).strip()) if m else ({}, text)
    return {**meta, "layer": meta.get("layer", "team"), "body": body, "path": str(path)}


def _applies(meta: dict, ctx: dict) -> bool:
    cond = meta.get("applies_when") or {}
    return all(ctx.get(k) in (v if isinstance(v, list) else [v]) for k, v in cond.items())


def _spec_for_role(role: str) -> Path:
    hits = [p for p in SPECS.glob("*.md") if p.stem.split("_", 1)[-1] == role]
    return hits[0]


def load(role: str, user_id, ctx: dict) -> list[dict]:
    layers = []
    pt = KN / role / "personal" / str(user_id) / "SKILL.md" if user_id else None
    if pt and pt.exists():
        layers.append(_parse(pt))
    ln = KN / role / "learned" / "SKILL.md"       # lớp học được (đã promote)
    if ln.exists():
        layers.append(_parse(ln))
    tm = KN / role / "team" / "SKILL.md"
    if tm.exists():
        layers.append(_parse(tm))
    selected = [s for s in layers if _applies(s, ctx)]
    selected.append(_parse(_spec_for_role(role)))   # _core từ specs, luôn cuối
    return selected


def versions(skills: list[dict]) -> dict:
    return {s.get("name", s["path"]): str(s.get("version", "0")) for s in skills}
