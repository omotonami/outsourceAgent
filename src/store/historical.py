"""Historical project DB. Nguồn của nhãn FACT cho Estimation, và đích của vòng
reconcile khi đóng dự án. Mỗi dự án đã đóng = 1 file JSON trong data/historical_projects/."""
import json, re, statistics, datetime
from pathlib import Path

ROOT = Path(__file__).parents[2]
DEFAULT_DIR = ROOT / "data/historical_projects"


def _dir(base):
    return Path(base) if base else DEFAULT_DIR


def _tok(s):
    return set(re.findall(r"\w+", (s or "").lower()))


def load_all(base=None):
    out = []
    for p in sorted(_dir(base).glob("*.json")):
        if p.name == "README.md":
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return out


def append(project, base=None):
    d = _dir(base); d.mkdir(parents=True, exist_ok=True)
    project.setdefault("closed_at", datetime.date.today().isoformat())
    (d / f"{project['id']}.json").write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return project


def lookup(item, domain=None, tech_stack=(), exclude_ids=frozenset(), base=None,
           min_overlap=2, k=5):
    """Tìm WBS item tương tự trong các dự án đã đóng (có hỗ trợ holdout qua exclude_ids)."""
    cand = []
    want = _tok(item)
    for p in load_all(base):
        if p["id"] in exclude_ids:
            continue
        dom_ok = 1 if (domain and p.get("domain") == domain) else 0
        for it in p.get("wbs", []):
            ov = len(want & _tok(it.get("item", "")))
            if ov >= min_overlap:
                cand.append((ov + dom_ok, p["id"], it))
    cand.sort(key=lambda x: x[0], reverse=True)
    top = cand[:k]
    if not top:
        return {"matches": [], "effort_md": None, "n": 0}
    efforts = [it.get("actual_md", it.get("estimate_md")) for _, _, it in top]
    efforts = [e for e in efforts if e is not None]
    return {
        "matches": [{"project_id": pid, "item": it.get("item"),
                     "actual_md": it.get("actual_md")} for _, pid, it in top],
        "effort_md": round(statistics.median(efforts), 1) if efforts else None,
        "n": len(top),
    }


def variance_ratios(domain=None, exclude_ids=frozenset(), base=None):
    """Tỉ lệ actual/estimate của các dự án đã đóng, để variance_model dựng cost_range."""
    out = []
    for p in load_all(base):
        if p["id"] in exclude_ids:
            continue
        if domain and p.get("domain") != domain:
            continue
        est, act = p.get("estimate_md_total"), p.get("actual_md_total")
        if est and act:
            out.append(act / est)
    return out
